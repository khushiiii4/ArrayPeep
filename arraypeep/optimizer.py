"""Optimization driver: walks accesses, rebuilds quadruples, emits report; L2/L3 switch.

Phase 7 part: analysis. For every array access of a generated
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

The listing passed in is never modified. The window check raises
``WindowError`` (an internal error) on an inconsistent descriptor instead of
returning a wrong window.

Phase 8 part (``optimize_l2``): for every optimizable access the window and the
access quadruple are replaced by the L2 code ``K + SUM D_s * e_s`` (quad_spec.md
7.1): terms with ``D_s = 0`` disappear, a term is the loop variable itself when
``D_s = 1`` and ``e_s * D_s`` (one multiplication) otherwise, terms are added
left to right outermost first, and the access is ``K[T]`` (``K[0]`` when no term
remains). A fallback access keeps its L1 code unchanged, and so does an access
nested inside the window of a fallback access. The result is a *new* listing
with new descriptors and loop table; temporaries of untouched code keep their
names and new temporaries get fresh numbers. L3 (Phase 9) is not here.
"""
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

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
    Indexed,
    LoopEntry,
    Operand,
    Quad,
    format_operand,
    is_temp,
    well_formedness_errors,
)
from .symbol_table import ArrayPeepError, SymbolTable

if TYPE_CHECKING:  # precompute is imported inside optimize_l2 (see there)
    from .precompute import AccessPlan

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
        """Quadruple positions covered by the window, ``first`` to ``last``."""
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
        """True when the access is eligible for optimization."""
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


# -- Phase 8: L2 code ------------------------------------------------------------------------


class L2BuildError(ArrayPeepError):
    """The rebuilt L2 listing is not well formed (a compiler bug): ``internal error: ...``."""

    def __init__(self, message: str) -> None:
        super().__init__(f"internal error: {message}")


@dataclass(frozen=True)
class L2Entry:
    """What Phase 8 did for one access (same order as ``analyze_accesses``)."""

    report: AccessReport
    plan: "AccessPlan | None"  # None for a fallback and for an access kept inside a fallback window
    applied: bool
    decision: str
    old_code: tuple[Quad, ...]  # the L1 window and access quadruple that were replaced (or kept)
    new_code: tuple[Quad, ...]  # the L2 window and access quadruple
    new_first: int  # positions of the new window and access in the L2 listing
    new_last: int
    new_access: int


@dataclass(frozen=True)
class L2Program:
    """The L2 listing with its descriptors and loop table (``code.level == "L2"``) and one entry per access."""

    code: GeneratedCode
    entries: list[L2Entry]


def _max_temp(quads: Sequence[Quad]) -> int:
    top = 0
    for q in quads:
        for operand in (q.arg1, q.arg2, q.result):
            if isinstance(operand, Indexed):
                operand = operand.index
            if is_temp(operand):
                top = max(top, int(operand[1:]))
    return top


def emit_l2(plan: "AccessPlan", fresh) -> tuple[list[Quad], Operand]:
    """ADDR quadruples that compute ``SUM D_s * e_s`` and the place ``T`` holding it (``0`` if no term).

    ``fresh()`` returns a new temporary name. One multiplication per term whose
    coefficient is not 1, one addition per term after the first.
    """
    out: list[Quad] = []
    place: Operand = 0
    for n, (var, d) in enumerate(plan.terms):
        term: Operand = var
        if d != 1:
            term = fresh()
            out.append(Quad("*", var, d, term, TAG_ADDR))
        if n == 0:
            place = term
        else:
            total = fresh()
            out.append(Quad("+", place, term, total, TAG_ADDR))
            place = total
    return out, place


def optimize_l2(code: GeneratedCode, symtab: SymbolTable) -> L2Program:
    """Rebuild ``code`` (an L1 or L0 listing) with every optimizable access at L2.

    ``code`` is not modified. Raises ``WindowError`` for inconsistent
    descriptors and ``L2BuildError`` if the result is ill formed.
    """
    from .precompute import make_plan  # imported here so that analysis alone (Phase 7) does not load it

    reports = analyze_accesses(code)
    descs = code.accesses
    fallback_spans = [(descs[r.index].first, descs[r.index].last) for r in reports if not r.optimizable]
    top = _max_temp(code.quads)

    def fresh() -> str:
        nonlocal top
        top += 1
        return f"T{top}"

    # window start -> (old access position, new quadruples, plan)
    replace_at: dict[int, tuple[int, list[Quad], "AccessPlan"]] = {}
    for r in reports:
        d = descs[r.index]
        if not r.optimizable or any(a <= d.first and d.access <= b for a, b in fallback_spans):
            continue
        plan = make_plan(symtab.get_array(r.array), r.forms, [e.var for e in r.loops])
        window, place = emit_l2(plan, fresh)
        old = code.quads[d.access - 1]
        cell = Indexed(plan.k, place)
        if d.is_write:
            access = Quad(OP_STORE, old.arg1, None, cell, TAG_BODY)
        else:
            access = Quad(OP_LOAD, cell, None, old.result, TAG_BODY)
        replace_at[d.first] = (d.access, window + [access], plan)

    out: list[Quad] = []
    new_pos: dict[int, int] = {}
    placed: dict[int, tuple[int, int, int]] = {}  # old window start -> (first, last, access) in the new listing
    p = 1
    while p <= len(code.quads):
        if p in replace_at:
            old_access, new_quads, _ = replace_at[p]
            first = len(out) + 1
            out.extend(new_quads)
            placed[p] = (first, len(out) - 1, len(out))
            p = old_access + 1
            continue
        out.append(code.quads[p - 1])
        new_pos[p] = len(out)
        p += 1

    def rewritten(d: AccessDescriptor) -> bool:
        return d.first in replace_at and replace_at[d.first][0] == d.access

    accesses: list[AccessDescriptor] = []
    for d in descs:
        if rewritten(d):
            first, last, access = placed[d.first]
            accesses.append(replace(d, first=first, last=last, access=access))
        else:
            first = new_pos[d.first]
            accesses.append(replace(d, first=first, last=first + (d.last - d.first), access=new_pos[d.access]))
    loops = [replace(e, for_pos=new_pos[e.for_pos], endfor_pos=new_pos[e.endfor_pos]) for e in code.loops]
    new_code = GeneratedCode(out, accesses, loops, "L2")
    problems = well_formedness_errors(out)
    if problems:
        raise L2BuildError(problems[0])
    validate_windows(out, accesses)

    entries = []
    for r in reports:
        d, nd = descs[r.index], accesses[r.index]
        if rewritten(d):
            plan = replace_at[d.first][2]
            decision = "L2 applied" if plan.terms else "L2 applied (constant address, no run-time arithmetic)"
        else:
            plan = None
            decision = (f"kept at L1 ({r.reason})" if not r.optimizable
                        else "kept at L1 (inside the window of a fallback access)")
        entries.append(
            L2Entry(
                r, plan, plan is not None, decision,
                tuple(code.quads[d.first - 1:d.access]), tuple(out[nd.first - 1:nd.access]),
                nd.first, nd.last, nd.access,
            )
        )
    return L2Program(new_code, entries)


def _quad_text(q: Quad) -> str:
    cells = [format_operand(q.arg1), format_operand(q.arg2), format_operand(q.result)]
    return f"{q.tag:<4} {q.op:<5} {cells[0]:<9} {cells[1]:<7} {cells[2]}".rstrip()


def format_l2_report(program: L2Program) -> str:
    """Optimization report with the Phase 8 lines (quad_spec.md 9.3); no trailing newline."""
    lines = ["== Optimization report ".ljust(LISTING_WIDTH, "=")]
    for e in program.entries:
        r = e.report
        lines.append(f" access {r.source}  ({'write' if r.is_write else 'read'}, line {r.line})")
        shown = [f"subscript {n} = " + (f.expr_text() if isinstance(f, LinearForm) else f.describe())
                 for n, f in enumerate(r.forms, start=1)]
        lines.append("   linear forms : " + " ; ".join(shown))
        if e.plan is not None:
            p = e.plan
            lines.append("   K = " + str(p.k) + "".join(f"   D_{v} = {d}" for v, d in p.ds)
                         + f"   (CONSPART {p.conspart}, D0 {p.d0})")
            lines.append(f"   address      : {p.expr_text()}")
        old = r.window
        lines.append(f"   window       : L1 quadruples {old.first}..{old.access} -> L2 quadruples {e.new_first}..{e.new_access}")
        if e.applied:
            for label, code, start in (("replaced (L1)", e.old_code, old.first), ("resulting (L2)", e.new_code, e.new_first)):
                for n, q in enumerate(code):
                    lines.append(f"   {label if n == 0 else '':<14} : {start + n:>3}  {_quad_text(q)}")
        lines.append(f"   decision     : {e.decision}")
    return "\n".join(lines)
