"""Loop optimization: hoisting, incremental update and profitability guard (L3).

Phase 9.1 analyses the loop structure of one access; it emits no code. The loops enclosing an access are ordered from outermost
(level 1) to innermost and paired with the ``D_s`` coefficients of its
address plan. Loops with ``D_s = 0`` are ignored. ``lambda`` is the innermost
loop with ``D_s != 0``: the address is invariant in every loop nested inside
it, so those loops need no address work. [OUR EXTENSION]

Phase 9.2 (hoisting, ``hoist_l2``) rewrites an L2 listing so that the partial
sum ``P_r = K + SUM_(s <= r) D_s * e_s`` of every active level ``r`` is
computed once per iteration of loop ``r``, immediately before the ``FOR`` of
level ``r + 1``, instead of inside the innermost loop. The innermost level
cannot be hoisted: its sum is computed in place, just before the access.
There are no incremental updates yet (Phase 9.3) and no profitability check
(Phase 9.4). [OUR EXTENSION]

Phase 9.3 (incremental update, ``increment_l2``) keeps one address temporary
``P_r`` per active level ``r`` of an access. It is initialised immediately
before the ``FOR`` of loop ``r`` (``P_r = P_outer + D_r * lo_r``, where
``P_outer`` is the temporary of the nearest outer active level or the
constant ``K``), used by the access (innermost active level) and increased by
``D_r`` immediately before the ``ENDFOR`` of loop ``r``. Because the step is 1,
``address(e + 1) = address(e) + D_r``, so no multiplication is left. [OUR EXTENSION]

Phase 9.4 (profitability guard, ``guarded_l3``) applies the incremental scheme
to an access only if it strictly lowers the arithmetic executed per iteration
of the access's innermost enclosing loop, compared with L2. Cost model: the
number of arithmetic quadruples (``+``, ``-``, ``*``) generated for the access
that lie inside the body of that loop. Initialisations before a ``FOR`` run
outside the loop they initialise, so only the increment of the innermost level
counts at L3. The decision is made per access. [OUR EXTENSION]
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from .codegen import GeneratedCode
from .linear_analysis import LinearForm
from .optimizer import L2Entry, L2Program, _max_temp, enclosing_loops
from .precompute import AccessPlan
from .quad import (
    ARITHMETIC_OPS,
    OP_LOAD,
    OP_STORE,
    TAG_ADDR,
    TAG_BODY,
    AccessDescriptor,
    Indexed,
    LoopEntry,
    Operand,
    LISTING_WIDTH,
    Quad,
    well_formedness_errors,
)
from .symbol_table import ArrayPeepError


@dataclass(frozen=True)
class LoopLevel:
    """One enclosing loop of an access: its level (1 = outermost), loop entry and ``D_s``."""

    level: int
    loop: LoopEntry
    ds: int

    @property
    def active(self) -> bool:
        """True when the address depends on this loop (``D_s != 0``)."""
        return self.ds != 0


@dataclass(frozen=True)
class LoopLevels:
    """Loop-level analysis of one access (Phase 9.1).

    ``levels`` holds every enclosing loop, outermost first, zeros included.
    ``lam`` is the innermost active level, or ``None`` when every ``D_s`` is
    zero (a constant address). ``invariant`` lists the loops nested inside
    ``lam`` (all enclosing loops when there is no ``lam``): the address does
    not change in them, so no address-update work belongs there.
    """

    levels: tuple[LoopLevel, ...]

    @property
    def active(self) -> tuple[LoopLevel, ...]:
        """The levels with ``D_s != 0``, outermost first."""
        return tuple(lv for lv in self.levels if lv.active)

    @property
    def lam(self) -> LoopLevel | None:
        """The innermost loop with ``D_s != 0``, or ``None`` if there is none."""
        active = self.active
        return active[-1] if active else None

    @property
    def invariant(self) -> tuple[LoopLevel, ...]:
        """The loops in which the address is invariant: those nested inside ``lam``."""
        lam = self.lam
        start = lam.level if lam is not None else 0
        return self.levels[start:]


class LoopLevelError(ArrayPeepError):
    """The loops and the plan of an access disagree (a compiler bug): ``internal error: ...``."""

    def __init__(self, message: str) -> None:
        super().__init__(f"internal error: {message}")


def analyze_loop_levels(plan: AccessPlan, loops: Sequence[LoopEntry]) -> LoopLevels:
    """Pair the enclosing ``loops`` (outermost first) with the ``D_s`` of ``plan``.

    Raises ``LoopLevelError`` if the plan does not list exactly these loop
    variables in this order.
    """
    plan_vars = [v for v, _ in plan.ds]
    loop_vars = [e.var for e in loops]
    if plan_vars != loop_vars:
        raise LoopLevelError(f"plan loop variables {plan_vars} differ from enclosing loops {loop_vars}")
    return LoopLevels(tuple(LoopLevel(n, e, d) for n, (e, (_, d)) in enumerate(zip(loops, plan.ds), start=1)))


def analyze_program_loops(program: L2Program) -> list[LoopLevels | None]:
    """One entry per access of ``program`` (same order): its loop levels, or ``None`` without a plan."""
    return [
        None if e.plan is None else analyze_loop_levels(e.plan, e.report.loops)
        for e in program.entries
    ]


# -- Phase 9.2: hoisting -----------------------------------------------------------------------

HOISTED_LEVEL = "L3-hoist"
"""``GeneratedCode.level`` of a listing produced by ``hoist_l2`` (hoisting only, no increments)."""


class HoistError(ArrayPeepError):
    """The hoisted listing is not well formed (a compiler bug): ``internal error: ...``."""

    def __init__(self, message: str) -> None:
        super().__init__(f"internal error: {message}")


@dataclass(frozen=True)
class Hoisted:
    """Quadruples of one partial sum ``P_r`` placed in front of the ``FOR`` of level ``r + 1``."""

    level: int  # r: the loop whose iteration recomputes the sum
    quads: tuple[Quad, ...]
    first: int  # position of the first quadruple in the hoisted listing


@dataclass(frozen=True)
class HoistedEntry:
    """What hoisting did for one access (same order as ``L2Program.entries``).

    ``levels`` is ``None`` when the access has no plan (fallback, or kept at
    L1 inside a fallback window); such an access is copied unchanged.
    ``hoisted`` are the pre-header partial sums, ``inplace`` the quadruples
    emitted just before the access (the sum of the innermost level, if it is
    active); ``access`` is the position of the access quadruple.
    """

    l2: L2Entry
    levels: LoopLevels | None
    hoisted: tuple[Hoisted, ...]
    inplace: tuple[Quad, ...]
    access: int
    decision: str


@dataclass(frozen=True)
class HoistedProgram:
    """Listing with hoisted partial sums (``code.level == HOISTED_LEVEL``) and one entry per access.

    ``code.accesses`` describe only the in-place part of each address
    (hoisted ``ADDR`` quadruples lie outside every window, so the Phase 7
    ``validate_windows`` does not apply to this listing); ``hoisted_positions``
    lists those quadruples.
    """

    code: GeneratedCode
    entries: list[HoistedEntry]
    hoisted_positions: frozenset[int]


def _max_addr_temp(quads: Sequence[Quad]) -> int:
    top = 0
    for q in quads:
        for operand in (q.arg1, q.arg2, q.result):
            if isinstance(operand, Indexed):
                operand = operand.index
            if isinstance(operand, str) and re.fullmatch(r"P[0-9]+", operand):
                top = max(top, int(operand[1:]))
    return top


def hoist_l2(l2: L2Program) -> HoistedProgram:
    """Rebuild the L2 listing ``l2`` with the partial sums of every planned access hoisted.

    Per access: ``prev = K``; for every active level ``r`` (outermost first)
    ``P_r = prev + D_r * e_r`` (a ``*`` only when ``D_r != 1``) and
    ``prev = P_r``. A level ``r`` smaller than the innermost enclosing loop
    is placed before the ``FOR`` of level ``r + 1``; the innermost level is
    placed just before the access. The access reads ``0[prev]``; with no
    active loop it is the constant address ``K[0]``, exactly as at L2. Every
    access gets its own temporaries. ``l2`` is not modified. Raises
    ``HoistError`` if the result is ill formed.
    """
    code = l2.code
    quads = code.quads
    top_t, top_p = _max_temp(quads), _max_addr_temp(quads)

    def fresh_t() -> str:
        nonlocal top_t
        top_t += 1
        return f"T{top_t}"

    def fresh_p() -> str:
        nonlocal top_p
        top_p += 1
        return f"P{top_p}"

    insert_before: dict[int, list[tuple[int, int, list[Quad]]]] = {}  # FOR position -> (entry, level, quads)
    replace_at: dict[int, tuple[int, list[Quad], list[Quad], int]] = {}  # window start -> (end, inplace, access, entry)
    levels_of: dict[int, LoopLevels] = {}
    for k, e in enumerate(l2.entries):
        if e.plan is None:
            continue
        loops = enclosing_loops(code.loops, e.new_access)
        lv = analyze_loop_levels(e.plan, loops)
        levels_of[k] = lv
        n = len(lv.levels)
        prev: Operand = e.plan.k
        inplace: list[Quad] = []
        for x in lv.active:
            group: list[Quad] = []
            term: Operand = x.loop.var
            if x.ds != 1:
                term = fresh_t()
                group.append(Quad("*", x.loop.var, x.ds, term, TAG_ADDR))
            total = fresh_p()
            group.append(Quad("+", prev, term, total, TAG_ADDR))
            prev = total
            if x.level < n:
                insert_before.setdefault(lv.levels[x.level].loop.for_pos, []).append((k, x.level, group))
            else:
                inplace = group
        old = quads[e.new_access - 1]
        cell = Indexed(e.plan.k, 0) if not lv.active else Indexed(0, prev)
        access = Quad(OP_STORE, old.arg1, None, cell, TAG_BODY) if old.op == OP_STORE else Quad(
            OP_LOAD, cell, None, old.result, TAG_BODY)
        replace_at[e.new_first] = (e.new_access, inplace, [access], k)

    out: list[Quad] = []
    new_pos: dict[int, int] = {}
    hoisted_at: dict[tuple[int, int], Hoisted] = {}
    placed: dict[int, tuple[int, int]] = {}  # entry -> (first in-place position, access position)
    p = 1
    while p <= len(quads):
        for k, r, group in insert_before.get(p, ()):
            hoisted_at[(k, r)] = Hoisted(r, tuple(group), len(out) + 1)
            out.extend(group)
        if p in replace_at:
            end, inplace, access, k = replace_at[p]
            first = len(out) + 1
            out.extend(inplace)
            out.extend(access)
            placed[k] = (first, len(out))
            p = end + 1
            continue
        out.append(quads[p - 1])
        new_pos[p] = len(out)
        p += 1

    accesses: list[AccessDescriptor] = []
    by_report = {e.report.index: k for k, e in enumerate(l2.entries)}
    for idx, d in enumerate(code.accesses):
        k = by_report[idx]
        if k in placed:
            first, access = placed[k]
            accesses.append(replace(d, first=first, last=access - 1, access=access))
        else:
            first = new_pos[d.first]
            accesses.append(replace(d, first=first, last=first + (d.last - d.first), access=new_pos[d.access]))
    loops = [replace(e, for_pos=new_pos[e.for_pos], endfor_pos=new_pos[e.endfor_pos]) for e in code.loops]
    new_code = GeneratedCode(out, accesses, loops, HOISTED_LEVEL)

    hoisted_positions = frozenset(
        pos for h in hoisted_at.values() for pos in range(h.first, h.first + len(h.quads))
    )
    problems = well_formedness_errors(out)
    if problems:
        raise HoistError(problems[0])
    in_window = {pos for d in accesses for pos in d.window}
    stray = [pos for pos, q in enumerate(out, 1)
             if q.tag == TAG_ADDR and pos not in in_window and pos not in hoisted_positions]
    if stray:
        raise HoistError(f"ADDR quadruples {stray} belong to no window and were not hoisted")

    entries = []
    for k, e in enumerate(l2.entries):
        if k not in levels_of:
            entries.append(HoistedEntry(e, None, (), (), new_pos[e.new_access], e.decision))
            continue
        lv = levels_of[k]
        first, access = placed[k]
        hs = tuple(hoisted_at[(k, x.level)] for x in lv.active if (k, x.level) in hoisted_at)
        if not lv.active:
            decision = "no active loop: constant address, nothing to hoist"
        elif hs:
            decision = "hoisted levels " + ", ".join(str(h.level) for h in hs)
        else:
            decision = "nothing to hoist (innermost loop only)"
        entries.append(HoistedEntry(e, lv, hs, tuple(out[first - 1:access - 1]), access, decision))
    return HoistedProgram(new_code, entries, hoisted_positions)


# -- Phase 9.3: incremental address updates ------------------------------------------------------

INCREMENTAL_LEVEL = "L3-incr"
"""``GeneratedCode.level`` of a listing produced by ``increment_l2`` (provisional until the stage switch)."""


@dataclass(frozen=True)
class LevelAddress:
    """The address temporary of one active level of one access, with its init and increment quadruples."""

    level: int
    temp: str  # P_r
    init: Quad  # immediately before the FOR of the loop
    init_pos: int  # position in the incremental listing
    incr: Quad  # ``P_r = P_r + D_r`` immediately before the ENDFOR of the loop
    incr_pos: int


@dataclass(frozen=True)
class IncrementalEntry:
    """What incremental update did for one access (same order as ``L2Program.entries``).

    ``levels`` is ``None`` for an access without a plan (copied unchanged).
    ``addresses`` has one ``LevelAddress`` per active level, outermost first;
    the last one is used by the access at position ``access``.
    """

    l2: L2Entry
    levels: LoopLevels | None
    addresses: tuple[LevelAddress, ...]
    access: int
    decision: str


@dataclass(frozen=True)
class IncrementalProgram:
    """Listing with incrementally updated addresses and one entry per access.

    Every address quadruple lies outside the access windows (all windows of
    rewritten accesses are empty), so ``validate_windows`` does not apply;
    ``addr_positions`` lists the generated init and increment quadruples.
    """

    code: GeneratedCode
    entries: list[IncrementalEntry]
    addr_positions: frozenset[int]


def increment_l2(l2: L2Program) -> IncrementalProgram:
    """Rebuild the L2 listing ``l2`` with every planned access updated incrementally.

    Per access and active level ``r`` (outermost first) with ``prev`` the
    temporary of the nearest outer active level: before ``FOR`` r,
    ``P_r = K + D_r * lo_r`` (a copy of a constant) when there is no outer
    active level, else ``P_r = prev + D_r * lo_r`` (an addition even when the
    constant is 0); before ``ENDFOR`` r, ``P_r = P_r + D_r``. The access reads
    ``0[P_innermost active]``; with no active loop it keeps the constant
    address ``K[0]`` of L2. Temporaries are never shared between accesses.
    ``l2`` is not modified. Raises ``HoistError`` if the result is ill formed
    or an ``ADDR`` quadruple is unaccounted for.
    """
    code = l2.code
    quads = code.quads
    top_p = _max_addr_temp(quads)

    def fresh_p() -> str:
        nonlocal top_p
        top_p += 1
        return f"P{top_p}"

    # position -> [(entry, level, quad)] to emit immediately before the FOR / ENDFOR at that position
    before_for: dict[int, list[tuple[int, int, Quad]]] = {}
    before_endfor: dict[int, list[tuple[int, int, Quad]]] = {}
    replace_at: dict[int, tuple[int, Quad, int]] = {}  # window start -> (old access position, access, entry)
    levels_of: dict[int, LoopLevels] = {}
    temps_of: dict[int, list[tuple[int, str]]] = {}
    for k, e in enumerate(l2.entries):
        if e.plan is None:
            continue
        lv = analyze_loop_levels(e.plan, enclosing_loops(code.loops, e.new_access))
        levels_of[k] = lv
        prev: str | None = None
        temps_of[k] = []
        for x in lv.active:
            temp = fresh_p()
            offset = x.ds * x.loop.lo
            init = (Quad(":=", e.plan.k + offset, None, temp, TAG_ADDR) if prev is None
                    else Quad("+", prev, offset, temp, TAG_ADDR))
            before_for.setdefault(x.loop.for_pos, []).append((k, x.level, init))
            before_endfor.setdefault(x.loop.endfor_pos, []).append(
                (k, x.level, Quad("+", temp, x.ds, temp, TAG_ADDR)))
            temps_of[k].append((x.level, temp))
            prev = temp
        old = quads[e.new_access - 1]
        cell = Indexed(0, prev) if prev is not None else Indexed(e.plan.k, 0)
        access = Quad(OP_STORE, old.arg1, None, cell, TAG_BODY) if old.op == OP_STORE else Quad(
            OP_LOAD, cell, None, old.result, TAG_BODY)
        replace_at[e.new_first] = (e.new_access, access, k)

    out: list[Quad] = []
    new_pos: dict[int, int] = {}
    init_pos: dict[tuple[int, int], int] = {}
    incr_pos: dict[tuple[int, int], int] = {}
    placed: dict[int, int] = {}  # entry -> access position
    p = 1
    while p <= len(quads):
        for k, r, q in before_for.get(p, ()):
            out.append(q)
            init_pos[(k, r)] = len(out)
        for k, r, q in before_endfor.get(p, ()):
            out.append(q)
            incr_pos[(k, r)] = len(out)
        if p in replace_at:
            end, access, k = replace_at[p]
            out.append(access)
            placed[k] = len(out)
            p = end + 1
            continue
        out.append(quads[p - 1])
        new_pos[p] = len(out)
        p += 1

    by_report = {e.report.index: k for k, e in enumerate(l2.entries)}
    accesses: list[AccessDescriptor] = []
    for idx, d in enumerate(code.accesses):
        k = by_report[idx]
        if k in placed:
            accesses.append(replace(d, first=placed[k], last=placed[k] - 1, access=placed[k]))
        else:
            first = new_pos[d.first]
            accesses.append(replace(d, first=first, last=first + (d.last - d.first), access=new_pos[d.access]))
    loops = [replace(e, for_pos=new_pos[e.for_pos], endfor_pos=new_pos[e.endfor_pos]) for e in code.loops]
    new_code = GeneratedCode(out, accesses, loops, INCREMENTAL_LEVEL)

    addr_positions = frozenset(list(init_pos.values()) + list(incr_pos.values()))
    problems = well_formedness_errors(out)
    if problems:
        raise HoistError(problems[0])
    in_window = {pos for d in accesses for pos in d.window}
    stray = [pos for pos, q in enumerate(out, 1)
             if q.tag == TAG_ADDR and pos not in in_window and pos not in addr_positions]
    if stray:
        raise HoistError(f"ADDR quadruples {stray} are neither in a window nor an init/increment")
    wrong_tag = [pos for pos in addr_positions if out[pos - 1].tag != TAG_ADDR]
    if wrong_tag:
        raise HoistError(f"init/increment quadruples {wrong_tag} are not tagged ADDR")

    entries = []
    for k, e in enumerate(l2.entries):
        if k not in levels_of:
            entries.append(IncrementalEntry(e, None, (), new_pos[e.new_access], e.decision))
            continue
        addrs = tuple(
            LevelAddress(r, t, out[init_pos[(k, r)] - 1], init_pos[(k, r)], out[incr_pos[(k, r)] - 1], incr_pos[(k, r)])
            for r, t in temps_of[k]
        )
        decision = ("no active loop: constant address, nothing to update" if not addrs
                    else "incremental levels " + ", ".join(str(a.level) for a in addrs))
        entries.append(IncrementalEntry(e, levels_of[k], addrs, placed[k], decision))
    return IncrementalProgram(new_code, entries, addr_positions)


# -- Phase 9.4: profitability guard --------------------------------------------------------------

GUARDED_LEVEL = "L3"
"""``GeneratedCode.level`` of a listing produced by ``guarded_l3`` (provisional until the stage switch)."""


def _arith_inside(quads: Sequence[Quad], first: int, loop: LoopEntry) -> int:
    """Arithmetic quadruples among ``quads`` (the first at position ``first``) lying inside the body of ``loop``."""
    return sum(
        1 for n, q in enumerate(quads, start=first)
        if q.op in ARITHMETIC_OPS and loop.for_pos < n < loop.endfor_pos
    )


@dataclass(frozen=True)
class GuardedEntry:
    """The guard's verdict for one access (same order as ``L2Program.entries``).

    ``l2_cost`` and ``l3_cost`` are the arithmetic quadruples executed per
    iteration of the innermost enclosing loop at L2 and at incremental L3;
    both are ``None`` when the access has no plan (fallback, or kept at L1
    inside a fallback window) and ``0`` for an access with no active loop.
    ``applied`` is true only when L3 was used for this access.
    """

    l2: L2Entry
    levels: LoopLevels | None
    l2_cost: int | None
    l3_cost: int | None
    applied: bool
    decision: str
    access: int  # position of the access quadruple in the guarded listing


@dataclass(frozen=True)
class GuardedProgram:
    """Listing in which each access is at L3 or kept as it was at L2 (``code.level == GUARDED_LEVEL``).

    ``addr_positions`` lists the init and increment quadruples of the accesses
    that use L3 (the generated ``ADDR`` quadruples outside every window).
    """

    code: GeneratedCode
    entries: list[GuardedEntry]
    addr_positions: frozenset[int]


def guarded_l3(l2: L2Program) -> GuardedProgram:
    """Apply incremental L3 only to the accesses of ``l2`` for which it strictly lowers the arithmetic.

    The cost of an access is measured on the code that ``increment_l2``
    would generate (L3) and on its L2 window: the arithmetic quadruples that
    run in the body of the innermost enclosing loop. L3 is used for an access
    only when its cost is strictly lower; the others stay exactly as they are
    in ``l2``. An access with no active loop (constant address) or without a
    plan is never changed. Decisions are independent per access: the listing
    is rebuilt with ``increment_l2`` from a copy of ``l2`` in which rejected
    accesses have no plan, so they are copied unchanged. ``l2`` is not modified.
    """
    full = increment_l2(l2)  # costs of every planned access; each access has its own temporaries
    verdicts: dict[int, tuple[int, int, LoopLevels]] = {}
    for k, (e, ie) in enumerate(zip(l2.entries, full.entries)):
        if e.plan is None:
            continue
        loops = enclosing_loops(l2.code.loops, e.new_access)
        if not loops:
            verdicts[k] = (0, 0, ie.levels)
            continue
        inner = loops[-1]
        l3_inner = full.code.loops[l2.code.loops.index(inner)]
        l2_cost = _arith_inside(e.new_code, e.new_first, inner)
        l3_cost = sum(_arith_inside([q], pos, l3_inner)
                      for a in ie.addresses for q, pos in ((a.init, a.init_pos), (a.incr, a.incr_pos)))
        verdicts[k] = (l2_cost, l3_cost, ie.levels)

    keep = {k for k, (c2, c3, lv) in verdicts.items() if not (c3 < c2)}
    filtered = L2Program(l2.code, [replace(e, plan=None) if k in keep else e for k, e in enumerate(l2.entries)])
    built = increment_l2(filtered)
    code = replace(built.code, level=GUARDED_LEVEL)

    entries = []
    for k, (e, ie) in enumerate(zip(l2.entries, built.entries)):
        if k not in verdicts:
            entries.append(GuardedEntry(e, None, None, None, False, e.decision, ie.access))
            continue
        c2, c3, lv = verdicts[k]
        if not lv.active:
            decision, applied = e.decision, False  # constant address: nothing to compare (decision of Phase 8)
        elif k in keep:
            what = "no address arithmetic to save" if c2 == 0 else "no arithmetic saved"
            decision = f"kept at L2 ({what}: {c2} at L2, {c3} at L3 per innermost iteration)"
            applied = False
        else:
            decision = f"L3 applied (address arithmetic per innermost iteration drops from {c2} to {c3})"
            applied = True
        entries.append(GuardedEntry(e, lv, c2, c3, applied, decision, ie.access))
    return GuardedProgram(code, entries, built.addr_positions)


def format_l3_report(program: GuardedProgram) -> str:
    """Optimization report with the guard's costs and decisions (quad_spec.md 9.3); no trailing newline."""
    lines = ["== Optimization report ".ljust(LISTING_WIDTH, "=")]
    for ge in program.entries:
        e = ge.l2
        r = e.report
        lines.append(f" access {r.source}  ({'write' if r.is_write else 'read'}, line {r.line})")
        shown = [f"subscript {n} = " + (f.expr_text() if isinstance(f, LinearForm) else f.describe())
                 for n, f in enumerate(r.forms, start=1)]
        lines.append("   linear forms : " + " ; ".join(shown))
        if e.plan is not None:
            p = e.plan
            lines.append("   K = " + str(p.k) + "".join(f"   D_{v} = {d}" for v, d in p.ds))
            lines.append("   arithmetic   : " + f"L2 {ge.l2_cost}, L3 {ge.l3_cost} per innermost iteration")
        lines.append(f"   decision     : {ge.decision}")
    return "\n".join(lines)


# -- Phase 9.8: stage selection ------------------------------------------------------------------

STAGE_L2 = "L2"
STAGE_HOIST = "hoist"
STAGE_L3 = "L3"
STAGES = (STAGE_L2, STAGE_HOIST, STAGE_L3)
"""The selectable stages, in increasing order of optimization: Phase 8 L2, hoisting only, guarded full L3."""


class StageError(ArrayPeepError):
    """An unsupported optimization stage name was requested."""

    def __init__(self, stage: object) -> None:
        super().__init__(f"unknown optimization stage {stage!r}; expected one of: {', '.join(STAGES)}")


@dataclass(frozen=True)
class StageResult:
    """The program of the selected ``stage`` (names in ``STAGES``).

    ``program`` is the result of the stage's own function, unchanged:
    the ``L2Program`` itself for ``"L2"``, a ``HoistedProgram`` for ``"hoist"``
    and a ``GuardedProgram`` for ``"L3"``; ``entries`` and ``code`` expose its
    per-access information and its listing.
    """

    stage: str
    program: "L2Program | HoistedProgram | GuardedProgram"

    @property
    def code(self) -> GeneratedCode:
        """The listing, descriptors and loop table of the selected stage."""
        return self.program.code

    @property
    def entries(self) -> list:
        """The per-access entries of the selected stage (same order for every stage)."""
        return self.program.entries


def optimize_stage(l2: L2Program, stage: str) -> StageResult:
    """Run the optimization ``stage`` on the L2 program ``l2`` and return it labelled with the stage.

    ``"L2"`` returns ``l2`` itself (the Phase 8 output), ``"hoist"`` calls
    ``hoist_l2`` (Phase 9.2) and ``"L3"`` calls ``guarded_l3`` (Phase 9.4,
    hoisting and incremental update with the profitability guard). Any other
    value raises ``StageError`` before anything is computed. ``l2`` is not
    modified.
    """
    if stage == STAGE_L2 and isinstance(stage, str):
        return StageResult(STAGE_L2, l2)
    if stage == STAGE_HOIST and isinstance(stage, str):
        return StageResult(STAGE_HOIST, hoist_l2(l2))
    if stage == STAGE_L3 and isinstance(stage, str):
        return StageResult(STAGE_L3, guarded_l3(l2))
    raise StageError(stage)