"""Optimization driver: walks accesses, rebuilds quadruples, emits report; L2/L3 switch.

Phase 7 part (this file): analysis only. For every array access of a generated
listing the driver

* lists the enclosing loops, outermost first (from the loop table),
* extracts the linear form of every subscript (``linear_analysis``),
* decides eligibility: an access is *optimizable* only if **all** its
  subscripts are linear, otherwise it is a *fallback* that stays at L1 and
  carries the reason of the first non-linear subscript,
* identifies and validates the peephole window (the contiguous quadruples that
  compute the subscripts and the address, ending just before the access
  quadruple), and
* produces one ``AccessReport`` per access.

Nothing is rewritten: the listing passed in is never modified. K, the D
coefficients, the L2 code (Phase 8) and the L3 code (Phase 9) are not here.
The window check raises ``WindowError`` (an internal error) on an inconsistent
descriptor instead of returning a wrong window.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from . import ast_nodes as ast
from .codegen import GeneratedCode
from .linear_analysis import LinearForm, NonLinear, analyze_subscript
from .quad import (
    LISTING_WIDTH,
    OP_LOAD,
    OP_STORE,
    TAG_ADDR,
    TAG_BODY,
    AccessDescriptor,
    LoopEntry,
    Quad,
)
from .symbol_table import ArrayPeepError

STATUS_OPTIMIZABLE = "optimizable"
STATUS_FALLBACK = "fallback"


class WindowError(ArrayPeepError):
    """A descriptor or listing is inconsistent (a compiler bug): ``internal error: ...``."""

    def __init__(self, message: str) -> None:
        super().__init__(f"internal error: {message}")


@dataclass(frozen=True)
class Window:
    """Validated peephole window of one access (1-based listing positions).

    ``first .. last`` is the window and ``access`` the indexed access
    quadruple (``last + 1``). ``addr`` lists the ``ADDR`` quadruples that
    belong to this access itself; ``nested`` lists the access positions of
    array reads nested inside its subscripts (their own ``ADDR`` quadruples
    and ``=[ ]`` lie inside the window but belong to them).
    """

    first: int
    last: int
    access: int
    addr: tuple[int, ...]
    nested: tuple[int, ...]

    @property
    def positions(self) -> range:
        return range(self.first, self.last + 1)


@dataclass(frozen=True)
class AccessReport:
    """Analysis result for one array access."""

    index: int  # position in the descriptor list (access completion order)
    array: str
    is_write: bool
    line: int
    source: str  # the access in MiniLoop notation, for example ``A[2 * i + 1, j - 1]``
    loops: tuple[LoopEntry, ...]  # enclosing loops, outermost first
    forms: tuple[LinearForm | NonLinear, ...]  # one per subscript
    status: str
    reason: str | None  # set for a fallback: the first non-linear subscript's reason
    window: Window

    @property
    def optimizable(self) -> bool:
        return self.status == STATUS_OPTIMIZABLE


# -- loops -------------------------------------------------------------------------------------


def enclosing_loops(loops: Sequence[LoopEntry], position: int) -> tuple[LoopEntry, ...]:
    """Loops whose ``FOR .. ENDFOR`` encloses the quadruple at ``position``, outermost first."""
    inside = [e for e in loops if e.for_pos < position < e.endfor_pos]
    return tuple(sorted(inside, key=lambda e: e.depth))


# -- windows -----------------------------------------------------------------------------------


def _span(d: AccessDescriptor) -> range:
    return range(d.first, d.access + 1)


def validate_windows(quads: Sequence[Quad], accesses: Sequence[AccessDescriptor]) -> None:
    """Check every descriptor against the listing; raise ``WindowError`` on the first inconsistency.

    Per access: the access position is inside the listing and holds ``=[ ]``
    (read) or ``[ ]=`` (write) tagged ``BODY``; the window ends immediately
    before it (``last + 1 == access``) and starts at or before it; every
    window quadruple is ``ADDR`` or the ``=[ ]`` of an access nested in it.
    Over the whole listing: access positions are distinct, every indexed
    quadruple has a descriptor, windows are nested or disjoint (never
    partially overlapping) and every ``ADDR`` quadruple lies in a window.
    """
    n = len(quads)
    seen: dict[int, int] = {}
    for k, d in enumerate(accesses):
        where = f"access {k} ({d.array})"
        if not 1 <= d.access <= n:
            raise WindowError(f"{where}: access position {d.access} is outside the listing of {n} quadruples")
        q = quads[d.access - 1]
        expected = OP_STORE if d.is_write else OP_LOAD
        if q.op != expected or q.tag != TAG_BODY:
            raise WindowError(f"{where}: quadruple {d.access} is {q.op!r}/{q.tag}, expected {expected!r}/BODY")
        if d.access in seen:
            raise WindowError(f"{where}: access position {d.access} is already used by access {seen[d.access]}")
        seen[d.access] = k
        if d.last + 1 != d.access:
            raise WindowError(f"{where}: window ends at {d.last} but the access is quadruple {d.access}")
        if not 1 <= d.first <= d.access:
            raise WindowError(f"{where}: window starts at {d.first}, outside 1..{d.access}")
    indexed = {p for p, q in enumerate(quads, 1) if q.op in (OP_LOAD, OP_STORE)}
    if indexed != set(seen):
        raise WindowError(f"indexed quadruples {sorted(indexed - set(seen))} have no access descriptor")
    for k, d in enumerate(accesses):
        for other in accesses[k + 1:]:
            a, b = _span(d), _span(other)
            disjoint = a.stop <= b.start or b.stop <= a.start
            nested = (a.start <= b.start and b.stop <= a.stop) or (b.start <= a.start and a.stop <= b.stop)
            if not (disjoint or nested):
                raise WindowError(f"windows of accesses to {d.array} and {other.array} overlap partially")
    covered: set[int] = set()
    for k, d in enumerate(accesses):
        nested_accesses = {o.access for o in accesses if o is not d and d.first <= o.first and o.access <= d.last}
        for p in d.window:
            q = quads[p - 1]
            covered.add(p)
            if q.tag != TAG_ADDR and not (q.op == OP_LOAD and p in nested_accesses):
                raise WindowError(
                    f"access {k} ({d.array}): window quadruple {p} is {q.op!r}/{q.tag}, "
                    "not an ADDR quadruple or a nested read"
                )
    missing = [p for p, q in enumerate(quads, 1) if q.tag == TAG_ADDR and p not in covered]
    if missing:
        raise WindowError(f"ADDR quadruples {missing} belong to no access window")


def identify_window(quads: Sequence[Quad], accesses: Sequence[AccessDescriptor], index: int) -> Window:
    """Validated window of ``accesses[index]``; the whole listing is validated first."""
    validate_windows(quads, accesses)
    d = accesses[index]
    inner = [o for o in accesses if o is not d and d.first <= o.first and o.access <= d.last]
    skip = {p for o in inner for p in _span(o)}
    addr = tuple(p for p in d.window if p not in skip and quads[p - 1].tag == TAG_ADDR)
    return Window(d.first, d.last, d.access, addr, tuple(sorted(o.access for o in inner)))


# -- driver ------------------------------------------------------------------------------------


def analyze_accesses(code: GeneratedCode) -> list[AccessReport]:
    """One ``AccessReport`` per array access of ``code``, in source order (outer access before its nested ones).

    ``code`` is not modified. Raises ``WindowError`` if a descriptor is inconsistent.
    """
    validate_windows(code.quads, code.accesses)
    reports = []
    for k, d in enumerate(code.accesses):
        loops = enclosing_loops(code.loops, d.access)
        names = [e.var for e in loops]
        forms = tuple(analyze_subscript(s, names) for s in d.subscripts)
        reason = None
        for n, form in enumerate(forms, start=1):
            if isinstance(form, NonLinear):
                reason = f"subscript {n}: {form.describe()}"
                break
        reports.append(
            AccessReport(
                index=k,
                array=d.array,
                is_write=d.is_write,
                line=d.subscripts[0].line if d.subscripts else 0,
                source=ast.format_expr(ast.ArrayRef(d.array, tuple(d.subscripts))),
                loops=loops,
                forms=forms,
                status=STATUS_FALLBACK if reason else STATUS_OPTIMIZABLE,
                reason=reason,
                window=identify_window(code.quads, code.accesses, k),
            )
        )
    reports.sort(key=lambda r: (r.window.first, -r.window.access))
    return reports


def format_report(reports: Sequence[AccessReport]) -> str:
    """Optimization report in the layout of quad_spec.md 9.3 (Phase 7 lines only); no trailing newline.

    The ``K``/``D`` line and the L2/L3 decision texts arrive with Phases 8 and 9;
    until then the decision says whether the access is eligible.
    """
    lines = ["== Optimization report ".ljust(LISTING_WIDTH, "=")]
    for r in reports:
        lines.append(f" access {r.source}  ({'write' if r.is_write else 'read'}, line {r.line})")
        lines.append("   enclosing    : " + (", ".join(f"{e.var} = {e.lo}..{e.hi}" for e in r.loops) or "(no loops)"))
        shown = [f"subscript {n} = " + (f.expr_text() if isinstance(f, LinearForm) else f.describe())
                 for n, f in enumerate(r.forms, start=1)]
        lines.append("   linear forms : " + " ; ".join(shown))
        w = r.window
        lines.append(
            f"   window       : quadruples {w.first}..{w.last}, access {w.access}"
            if w.positions else f"   window       : empty, access {w.access}"
        )
        lines.append(
            "   decision     : eligible (all subscripts linear)"
            if r.optimizable else f"   decision     : kept at L1 ({r.reason})"
        )
    return "\n".join(lines)
