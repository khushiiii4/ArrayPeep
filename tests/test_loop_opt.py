"""Tests for arraypeep.loop_opt (Phase 9.1: loop ordering, zero coefficients, lambda).

Expected coefficients come from the roadmap table on page 23 and the test
matrix T03, T04, T07, T08, T11 and T18 (pages 35).
"""
import unittest

from arraypeep.codegen import generate
from arraypeep.loop_opt import LoopLevelError, analyze_loop_levels, analyze_program_loops
from arraypeep.optimizer import optimize_l2
from arraypeep.parser import parse
from arraypeep.precompute import AccessPlan
from arraypeep.quad import LoopEntry
from arraypeep.semantic import analyze

import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
A = "array A[1:10, 1:20] at 1000;\n"


def levels_of(source: str):
    program = parse(source)
    table = analyze(program)
    code = generate(program, table, "L1")
    result = optimize_l2(code, table)
    return analyze_program_loops(result)


def summary(lv):
    return (
        [(x.level, x.loop.var, x.ds) for x in lv.levels],
        [x.loop.var for x in lv.active],
        None if lv.lam is None else lv.lam.loop.var,
        [x.loop.var for x in lv.invariant],
    )


class OrderingTests(unittest.TestCase):
    def test_levels_outermost_first_with_ds(self):
        (lv,) = levels_of(A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i+1, j-1]; end end")
        levels, active, lam, invariant = summary(lv)
        self.assertEqual(levels, [(1, "i", 40), (2, "j", 1)])
        self.assertEqual(active, ["i", "j"])
        self.assertEqual(lam, "j")
        self.assertEqual(invariant, [])

    def test_levels_follow_nesting_not_source_names(self):
        # j is the outer loop here: level order is the nesting order
        (lv,) = levels_of("array S[1:8, 1:8] at 300;\nfor j = 1 to 8 do for i = 1 to 8 do s = s + S[j, i]; end end")
        self.assertEqual([(x.level, x.loop.var, x.ds) for x in lv.levels], [(1, "j", 8), (2, "i", 1)])
        self.assertEqual(lv.lam.loop.var, "i")

    def test_enclosing_loops_only(self):
        src = A + "for i = 1 to 3 do s = s + A[i, 1]; for j = 1 to 3 do s = s + A[i, j]; end end"
        first, second = levels_of(src)
        self.assertEqual([x.loop.var for x in first.levels], ["i"])
        self.assertEqual([x.loop.var for x in second.levels], ["i", "j"])

    def test_depth_four(self):
        src = (
            "array H[0:1, 0:1, 0:1, 0:2] at 0;\n"
            "for a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do "
            "s = s + H[a, b, c, d]; end end end end"
        )
        (lv,) = levels_of(src)
        self.assertEqual([(x.level, x.loop.var, x.ds) for x in lv.levels], [(1, "a", 12), (2, "b", 6), (3, "c", 3), (4, "d", 1)])
        self.assertEqual(lv.lam.level, 4)


class ZeroCoefficientTests(unittest.TestCase):
    def test_inner_zero_is_invariant(self):
        # T08: A[i, 5]; Dj = 0, so lambda is the i loop and the j loop is invariant
        (lv,) = levels_of(A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, 5]; end end")
        levels, active, lam, invariant = summary(lv)
        self.assertEqual(levels, [(1, "i", 20), (2, "j", 0)])
        self.assertEqual(active, ["i"])
        self.assertEqual(lam, "i")
        self.assertEqual(invariant, ["j"])

    def test_outer_zero_is_ignored(self):
        (lv,) = levels_of(A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[3, j]; end end")
        levels, active, lam, invariant = summary(lv)
        self.assertEqual(levels, [(1, "i", 0), (2, "j", 1)])
        self.assertEqual(active, ["j"])
        self.assertEqual(lam, "j")
        self.assertEqual(invariant, [])

    def test_middle_zero_is_ignored(self):
        # T11 X[i,k]: Ds = (4, 0, 1)
        src = (
            "array X[1:4, 1:4] at 0;\n"
            "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + X[i, k]; end end end"
        )
        (lv,) = levels_of(src)
        levels, active, lam, invariant = summary(lv)
        self.assertEqual(levels, [(1, "i", 4), (2, "j", 0), (3, "k", 1)])
        self.assertEqual(active, ["i", "k"])
        self.assertEqual(lam, "k")
        self.assertEqual(invariant, [])

    def test_trailing_zeros_all_invariant(self):
        # T11 Z[i,j]: Ds = (4, 1, 0); only the k loop is invariant
        src = (
            "array Z[1:4, 1:4] at 0;\n"
            "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + Z[i, j]; end end end"
        )
        (lv,) = levels_of(src)
        self.assertEqual(summary(lv)[2:], ("j", ["k"]))

    def test_negative_coefficient_is_active(self):
        (lv,) = levels_of(A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[11-i, j]; end end")
        self.assertEqual([x.ds for x in lv.levels], [-20, 1])
        self.assertEqual([x.loop.var for x in lv.active], ["i", "j"])


class NoActiveLoopTests(unittest.TestCase):
    def test_no_loops_at_all(self):
        (lv,) = levels_of(A + "x = A[3, 4];")
        self.assertEqual(lv.levels, ())
        self.assertEqual(lv.active, ())
        self.assertIsNone(lv.lam)
        self.assertEqual(lv.invariant, ())

    def test_loops_but_constant_address(self):
        (lv,) = levels_of(A + "for i = 1 to 3 do for j = 1 to 3 do s = s + A[3, 4]; end end")
        levels, active, lam, invariant = summary(lv)
        self.assertEqual(levels, [(1, "i", 0), (2, "j", 0)])
        self.assertEqual(active, [])
        self.assertIsNone(lam)
        self.assertEqual(invariant, ["i", "j"])


class ProgramTests(unittest.TestCase):
    def test_fallback_has_no_levels(self):
        src = "array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i*j]; end end"
        self.assertEqual(levels_of(src), [None])

    def test_one_entry_per_access_in_order(self):
        src = (
            "array A[1:4, 1:5] at 0; array B[1:4, 1:5] at 100;\n"
            "for i = 1 to 4 do for j = 1 to 5 do A[i, j] = B[i, 1]; end end"
        )
        result = levels_of(src)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r is not None for r in result))

    def test_levels_are_not_mutating_the_listing(self):
        program = parse(A + "for i = 1 to 4 do s = s + A[i, 2]; end")
        table = analyze(program)
        l2 = optimize_l2(generate(program, table, "L1"), table)
        before = list(l2.code.quads)
        analyze_program_loops(l2)
        self.assertEqual(l2.code.quads, before)


class MismatchTests(unittest.TestCase):
    def loop(self, var, depth):
        return LoopEntry(var, 1, 3, depth, 0, 9)

    def plan(self, *pairs):
        return AccessPlan("A", 0, 0, 0, tuple(pairs))

    def test_matching_pair_ok(self):
        lv = analyze_loop_levels(self.plan(("i", 2), ("j", 0)), [self.loop("i", 1), self.loop("j", 2)])
        self.assertEqual(lv.lam.loop.var, "i")

    def test_wrong_order_rejected(self):
        with self.assertRaisesRegex(LoopLevelError, "internal error"):
            analyze_loop_levels(self.plan(("i", 1), ("j", 1)), [self.loop("j", 1), self.loop("i", 2)])

    def test_wrong_count_rejected(self):
        with self.assertRaises(LoopLevelError):
            analyze_loop_levels(self.plan(("i", 1)), [self.loop("i", 1), self.loop("j", 2)])


if __name__ == "__main__":
    unittest.main()


# -- Phase 9.2: hoisting -------------------------------------------------------------------------

import itertools  # noqa: E402

from arraypeep.interpreter import execute  # noqa: E402
from arraypeep.loop_opt import HOISTED_LEVEL, hoist_l2  # noqa: E402
from arraypeep.quad import OP_ENDFOR, OP_FOR, format_listing, is_addr_temp, is_temp  # noqa: E402


def hoisted_of(source: str):
    program = parse(source)
    table = analyze(program)
    l2 = optimize_l2(generate(program, table, "L1"), table)
    return l2, hoist_l2(l2), table


def real_scalars(result):
    return {k: v for k, v in result.scalars.items() if not (is_temp(k) or is_addr_temp(k))}


def same_behaviour(test, source: str):
    """Run L2 and the hoisted listing; scalars, memory and address trace must be equal."""
    l2, h, table = hoisted_of(source)
    a = execute(l2.code.quads, table.arrays, "L2")
    b = execute(h.code.quads, table.arrays, HOISTED_LEVEL)
    test.assertEqual(real_scalars(a), real_scalars(b))
    test.assertEqual(a.memory, b.memory)
    test.assertEqual(a.trace, b.trace)
    return l2, h, a, b


def ops(quads):
    return [(q.op, q.arg1, q.arg2, q.result) for q in quads]


class HoistNestedTests(unittest.TestCase):
    SRC = A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end"

    def test_two_level_placement(self):
        _, h, _, _ = same_behaviour(self, self.SRC)
        listing = h.code.quads
        # P1 = 979 + 20*i goes in the i body immediately before FOR j
        (entry,) = h.entries
        (hoist,) = entry.hoisted
        self.assertEqual(hoist.level, 1)
        end = hoist.first + len(hoist.quads)  # 1-based position directly after the hoisted group
        self.assertEqual(listing[end - 2].op, "+")
        self.assertEqual(listing[end - 1].op, OP_FOR)
        self.assertEqual(listing[end - 1].arg1, "j")
        self.assertEqual([q.op for q in hoist.quads], ["*", "+"])
        self.assertEqual(hoist.quads[0].arg2, 20)
        self.assertEqual(hoist.quads[1].arg1, 979)  # P_0 = K

    def test_innermost_level_in_place_and_chained(self):
        _, h, _, _ = same_behaviour(self, self.SRC)
        (entry,) = h.entries
        (hoist,) = entry.hoisted
        p1 = hoist.quads[-1].result
        # j has D = 1: one addition P2 = P1 + j just before the access, no multiplication
        self.assertEqual(len(entry.inplace), 1)
        q = entry.inplace[0]
        self.assertEqual((q.op, q.arg1, q.arg2), ("+", p1, "j"))
        access = h.code.quads[entry.access - 1]
        self.assertEqual(access.arg1.base, 0)
        self.assertEqual(access.arg1.index, q.result)
        self.assertEqual(h.code.quads[entry.access - 2], q)

    def test_hoisted_code_is_outside_the_inner_loop(self):
        _, h, _, _ = same_behaviour(self, self.SRC)
        loops = {e.var: e for e in h.code.loops}
        (entry,) = h.entries
        (hoist,) = entry.hoisted
        self.assertTrue(loops["i"].for_pos < hoist.first < loops["j"].for_pos)

    def test_executed_counts_match_hand_calculation(self):
        _, _, a, b = same_behaviour(self, self.SRC)
        # L2: per access 20*i (mult) + add; L3-hoist: mult+add once per i, one add per access
        self.assertEqual(a.counters.multiplications, 200)
        self.assertEqual(b.counters.multiplications, 10)

    def test_three_levels(self):
        src = (
            "array M[0:3, 0:4, 0:5] at 0;\n"
            "for i = 0 to 3 do for j = 0 to 4 do for k = 0 to 5 do s = s + M[i, j, k]; end end end"
        )
        _, h, _, _ = same_behaviour(self, src)
        (entry,) = h.entries
        self.assertEqual([x.level for x in entry.hoisted], [1, 2])
        for hoist in entry.hoisted:
            nxt = h.code.quads[hoist.first + len(hoist.quads) - 1]
            self.assertEqual(nxt.op, OP_FOR)
        self.assertEqual(h.code.quads[entry.hoisted[0].first + len(entry.hoisted[0].quads) - 1].arg1, "j")
        self.assertEqual(h.code.quads[entry.hoisted[1].first + len(entry.hoisted[1].quads) - 1].arg1, "k")
        # P2 reads P1: the dependency chain
        p1 = entry.hoisted[0].quads[-1].result
        self.assertEqual(entry.hoisted[1].quads[-1].arg1, p1)

    def test_depth_four(self):
        src = (
            "array H[0:1, 0:1, 0:1, 0:2] at 0;\n"
            "for a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do "
            "s = s + H[a, b, c, d]; end end end end"
        )
        _, h, _, _ = same_behaviour(self, src)
        self.assertEqual([x.level for x in h.entries[0].hoisted], [1, 2, 3])

    def test_write_access(self):
        src = A + "for i = 1 to 4 do for j = 1 to 5 do A[i, j] = i * 100 + j; end end"
        _, h, a, _ = same_behaviour(self, src)
        self.assertEqual(h.code.quads[h.entries[0].access - 1].op, "[ ]=")

    def test_negative_and_offset_coefficients(self):
        for subs in ("11 - i, j", "2*i + 1, j - 1"):
            with self.subTest(subs=subs):
                same_behaviour(self, A + f"for i = 1 to 4 do for j = 2 to 20 do s = s + A[{subs}]; end end")

    def test_listing_is_not_modified_and_l2_unchanged(self):
        program = parse(self.SRC)
        table = analyze(program)
        l2 = optimize_l2(generate(program, table, "L1"), table)
        before = (list(l2.code.quads), list(l2.code.accesses), list(l2.code.loops))
        hoist_l2(l2)
        self.assertEqual((l2.code.quads, l2.code.accesses, l2.code.loops), before)


class HoistZeroCoefficientTests(unittest.TestCase):
    def test_inner_zero_stays_in_outer_body(self):
        # T08: A[i, 5], Dj = 0: P1 is computed before FOR j and the j loop only reads it
        l2, h, a, b = same_behaviour(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, 5]; end end")
        (entry,) = h.entries
        self.assertEqual([x.level for x in entry.hoisted], [1])
        self.assertEqual(entry.inplace, ())
        access = h.code.quads[entry.access - 1]
        self.assertEqual(access.arg1.base, 0)
        self.assertEqual(access.arg1.index, entry.hoisted[0].quads[-1].result)
        # the hoisted sum runs 10 times; the old in-place form ran 200 times
        self.assertEqual(b.counters.multiplications, 10)
        self.assertEqual(a.counters.multiplications, 200)

    def test_outer_zero_is_skipped(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[3, j]; end end")
        (entry,) = h.entries
        self.assertEqual(entry.hoisted, ())
        self.assertEqual(len(entry.inplace), 1)
        # first (and only) active level starts from the constant K
        self.assertEqual(entry.inplace[0].arg1, entry.l2.plan.k)

    def test_middle_zero(self):
        src = (
            "array X[1:4, 1:4] at 0;\n"
            "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + X[i, k]; end end end"
        )
        _, h, _, _ = same_behaviour(self, src)
        (entry,) = h.entries
        # Ds = (4, 0, 1): P1 before FOR j, P3 in place; the j level adds nothing
        self.assertEqual([x.level for x in entry.hoisted], [1])
        self.assertEqual(len(entry.inplace), 1)

    def test_trailing_zeros(self):
        src = (
            "array Z[1:4, 1:4] at 0;\n"
            "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + Z[i, j]; end end end"
        )
        _, h, _, _ = same_behaviour(self, src)
        (entry,) = h.entries
        self.assertEqual([x.level for x in entry.hoisted], [1, 2])
        self.assertEqual(entry.inplace, ())
        # P2 (level 2, D=1) is placed before FOR k
        last = entry.hoisted[1]
        self.assertEqual(h.code.quads[last.first + len(last.quads) - 1].arg1, "k")

    def test_no_active_loop_is_constant_address(self):
        l2, h, _, _ = same_behaviour(self, A + "for i = 1 to 3 do for j = 1 to 3 do s = s + A[3, 4]; end end")
        (entry,) = h.entries
        self.assertEqual((entry.hoisted, entry.inplace), ((), ()))
        self.assertEqual(h.code.quads, l2.code.quads)  # identical to L2

    def test_no_loops_at_all(self):
        l2, h, _, _ = same_behaviour(self, A + "x = A[3, 4];")
        self.assertEqual(h.code.quads, l2.code.quads)
        self.assertEqual(h.entries[0].decision, "no active loop: constant address, nothing to hoist")

    def test_one_dimensional_innermost_only(self):
        _, h, _, _ = same_behaviour(self, "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end")
        (entry,) = h.entries
        self.assertEqual(entry.hoisted, ())
        self.assertEqual(len(entry.inplace), 1)  # no hoisting is possible with a single loop


class HoistMultipleAccessTests(unittest.TestCase):
    SRC = (
        "array A[1:4, 1:5] at 0; array B[1:4, 1:5] at 100; array C[1:4, 1:5] at 200;\n"
        "for i = 1 to 4 do for j = 1 to 5 do C[i, j] = A[i, j] + B[i, j]; end end"
    )

    def test_each_access_has_its_own_temporaries(self):
        _, h, _, _ = same_behaviour(self, self.SRC)
        written = []
        for entry in h.entries:
            names = {q.result for hz in entry.hoisted for q in hz.quads} | {q.result for q in entry.inplace}
            written.append(names)
        self.assertEqual(len(h.entries), 3)
        for a, b in itertools.combinations(written, 2):
            self.assertTrue(a.isdisjoint(b))

    def test_all_hoists_precede_the_same_for(self):
        _, h, _, _ = same_behaviour(self, self.SRC)
        quads = h.code.quads
        for_j = next(p for p, q in enumerate(quads, 1) if q.op == OP_FOR and q.arg1 == "j")
        # three accesses: three hoisted groups, contiguous, directly in front of FOR j
        count = sum(len(hz.quads) for e in h.entries for hz in e.hoisted)
        self.assertEqual(sorted(h.hoisted_positions), list(range(for_j - count, for_j)))

    def test_mixed_levels_and_fallback(self):
        src = (
            "array A[1:4, 1:5] at 0; array V[0:99] at 500;\n"
            "for i = 1 to 4 do for j = 1 to 5 do V[i * j] = A[i, j] + A[i, 2]; end end"
        )
        _, h, _, _ = same_behaviour(self, src)
        fallback = [e for e in h.entries if e.levels is None]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0].decision, fallback[0].l2.decision)

    def test_fallback_window_is_untouched(self):
        src = (
            "array A[1:4, 1:5] at 0; array V[0:1100] at 5000;\n"
            "for i = 1 to 4 do for j = 1 to 5 do s = s + V[A[i, j] + 1]; end end"
        )
        l2, h, _, _ = same_behaviour(self, src)
        self.assertEqual(h.code.quads, l2.code.quads)

    def test_two_accesses_with_different_loop_sets(self):
        src = A + "for i = 1 to 3 do s = s + A[i, 1]; for j = 1 to 3 do s = s + A[i, j]; end end"
        _, h, _, _ = same_behaviour(self, src)
        self.assertEqual([x.level for x in h.entries[0].hoisted], [])
        self.assertEqual([x.level for x in h.entries[1].hoisted], [1])

    def test_matrix_multiply(self):
        src = (
            "array Z[1:4, 1:4] at 32; array X[1:4, 1:4] at 0; array Y[1:4, 1:4] at 16;\n"
            "for i = 1 to 4 do for j = 1 to 4 do Z[i, j] = 0; "
            "for k = 1 to 4 do Z[i, j] = Z[i, j] + X[i, k] * Y[k, j]; end end end"
        )
        _, h, a, b = same_behaviour(self, src)
        self.assertTrue(all(e.levels is not None for e in h.entries))
        self.assertEqual(a.counters.loads, b.counters.loads)
        self.assertEqual(a.counters.stores, b.counters.stores)


class HoistZeroTripTests(unittest.TestCase):
    def test_zero_trip_inner_loop(self):
        # j runs zero times; the hoisted sum before FOR j is still executed, but touches no memory
        l2, h, a, b = same_behaviour(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        self.assertEqual(b.trace, [])
        self.assertEqual(a.trace, [])

    def test_zero_trip_outer_loop(self):
        _, h, _, b = same_behaviour(self, A + "for i = 5 to 1 do for j = 1 to 3 do s = s + A[i, j]; end end")
        self.assertEqual(b.trace, [])

    def test_single_iteration_loops(self):
        same_behaviour(self, "array U[1:1, 1:5] at 50;\nfor i = 1 to 1 do for j = 1 to 5 do s = s + U[i, j]; end end")

    def test_hoisted_sum_has_no_memory_access(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        for pos in h.hoisted_positions:
            self.assertIn(h.code.quads[pos - 1].op, ("+", "*"))


class HoistStructureTests(unittest.TestCase):
    def test_level_and_well_formed(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        self.assertEqual(h.code.level, HOISTED_LEVEL)

    def test_loop_table_matches_listing(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        for e in h.code.loops:
            self.assertEqual(h.code.quads[e.for_pos - 1].op, OP_FOR)
            self.assertEqual(h.code.quads[e.for_pos - 1].arg1, e.var)
            self.assertEqual(h.code.quads[e.endfor_pos - 1].op, OP_ENDFOR)

    def test_descriptors_point_at_accesses(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        for d in h.code.accesses:
            self.assertIn(h.code.quads[d.access - 1].op, ("=[ ]", "[ ]="))
            self.assertEqual(d.last + 1, d.access)

    def test_every_addr_quad_is_in_a_window_or_hoisted(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[2*i+1, j-1]; end end")
        window = {p for d in h.code.accesses for p in d.window}
        for p, q in enumerate(h.code.quads, 1):
            if q.tag == "ADDR":
                self.assertTrue(p in window or p in h.hoisted_positions)

    def test_all_example_programs_equal_l2(self):
        for path in sorted((ROOT / "examples").glob("*.ml")):
            with self.subTest(example=path.name):
                same_behaviour(self, path.read_text(encoding="utf-8"))

    def test_listing_prints(self):
        _, h, _, _ = same_behaviour(self, A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        self.assertIn("FOR", format_listing(h.code.quads))


# -- Phase 9.3: incremental address updates ------------------------------------------------------

from arraypeep.loop_opt import INCREMENTAL_LEVEL, increment_l2  # noqa: E402


def incremental_of(source: str):
    program = parse(source)
    table = analyze(program)
    l2 = optimize_l2(generate(program, table, "L1"), table)
    return l2, increment_l2(l2), table


def same_as_hoist(test, source: str):
    """Run the hoisting-only and the incremental listing; scalars, memory and trace must be equal."""
    l2, inc, table = incremental_of(source)
    hoisted = execute(hoist_l2(l2).code.quads, table.arrays, HOISTED_LEVEL)
    result = execute(inc.code.quads, table.arrays, INCREMENTAL_LEVEL)
    test.assertEqual(real_scalars(hoisted), real_scalars(result))
    test.assertEqual(hoisted.memory, result.memory)
    test.assertEqual(hoisted.trace, result.trace)
    return l2, inc, hoisted, result


def following(h, pos):
    """The quadruple after position ``pos`` (1-based)."""
    return h.code.quads[pos]


class IncrementalPlacementTests(unittest.TestCase):
    T04 = A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end"

    def test_t04_listing_shape(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        shape = [(q.op, q.arg1, q.arg2, q.result) for q in inc.code.quads if q.tag == "ADDR"]
        self.assertEqual(shape, [(":=", 1038, None, "P1"), ("+", "P1", 2, "P2"), ("+", "P2", 1, "P2"),
                                 ("+", "P1", 40, "P1")])

    def test_init_is_immediately_before_its_for(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        (entry,) = inc.entries
        outer, inner = entry.addresses
        self.assertEqual(following(inc, outer.init_pos).op, OP_FOR)
        self.assertEqual(following(inc, outer.init_pos).arg1, "i")
        self.assertEqual(following(inc, inner.init_pos).op, OP_FOR)
        self.assertEqual(following(inc, inner.init_pos).arg1, "j")
        self.assertEqual(outer.init, inc.code.quads[outer.init_pos - 1])

    def test_increment_is_immediately_before_its_endfor(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        (entry,) = inc.entries
        outer, inner = entry.addresses
        self.assertEqual((following(inc, inner.incr_pos).op, following(inc, inner.incr_pos).arg1), (OP_ENDFOR, "j"))
        self.assertEqual((following(inc, outer.incr_pos).op, following(inc, outer.incr_pos).arg1), (OP_ENDFOR, "i"))
        self.assertEqual((inner.incr.arg1, inner.incr.arg2, inner.incr.result), (inner.temp, 1, inner.temp))
        self.assertEqual((outer.incr.arg1, outer.incr.arg2, outer.incr.result), (outer.temp, 40, outer.temp))

    def test_access_comes_before_the_increment_and_uses_innermost_temp(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        (entry,) = inc.entries
        inner = entry.addresses[-1]
        access = inc.code.quads[entry.access - 1]
        self.assertEqual((access.arg1.base, access.arg1.index), (0, inner.temp))
        self.assertLess(entry.access, inner.incr_pos)

    def test_inner_level_is_reinitialised_from_the_current_outer_temp(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        (entry,) = inc.entries
        outer, inner = entry.addresses
        # the init is inside the i loop (between FOR i and FOR j), so it runs once per i iteration
        loops = {e.var: e for e in inc.code.loops}
        self.assertTrue(loops["i"].for_pos < inner.init_pos < loops["j"].for_pos)
        self.assertEqual(inner.init.arg1, outer.temp)
        # the outer init is before FOR i, once; the outer increment is after the j loop ends
        self.assertLess(outer.init_pos, loops["i"].for_pos)
        self.assertGreater(outer.incr_pos, loops["j"].endfor_pos)

    def test_no_multiplication_and_no_loop_variable_in_address_code(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        for pos in inc.addr_positions:
            q = inc.code.quads[pos - 1]
            self.assertNotEqual(q.op, "*")
            self.assertNotIn(q.arg1, ("i", "j"))
            self.assertNotIn(q.arg2, ("i", "j"))

    def test_every_addr_quad_is_accounted_for_and_tagged(self):
        _, inc, _, _ = same_as_hoist(self, self.T04)
        addr = {p for p, q in enumerate(inc.code.quads, 1) if q.tag == "ADDR"}
        self.assertEqual(addr, set(inc.addr_positions))
        self.assertEqual(inc.code.level, INCREMENTAL_LEVEL)

    def test_t04_predicted_counts(self):
        _, _, hoisted, result = same_as_hoist(self, self.T04)
        c = result.counters
        self.assertEqual(c.multiplications, 0)
        addr_adds = 76 + 4 + 4  # P2 += 1 (76), P2 = P1 + 2 (4), P1 += 40 (4)
        # plus the body's `s = s + A` (76 additions) and the constant initialisation of P1 (a copy)
        self.assertEqual(c.additions, addr_adds + 76)
        self.assertEqual(c.loads, 76)
        self.assertEqual(len(result.trace), 76)
        self.assertEqual(result.trace[0], ("R", 1040))
        self.assertEqual(result.trace[-1], ("R", 1178))

    def test_nonzero_lower_bound_init_offsets(self):
        # j = 2..20: P2 = P1 + D_j*lo_j = P1 + 2; i = 1..4: P1 = K + 40*1
        _, inc, _, _ = same_as_hoist(self, self.T04)
        (entry,) = inc.entries
        self.assertEqual(entry.addresses[0].init.arg1, 998 + 40)
        self.assertEqual(entry.addresses[1].init.arg2, 2)


class IncrementalNestedTests(unittest.TestCase):
    def test_identity_two_dimensional_matches_roadmap_figure(self):
        _, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end")
        shape = [(q.op, q.arg1, q.arg2, q.result) for q in inc.code.quads if q.tag == "ADDR"]
        self.assertEqual(shape, [(":=", 999, None, "P1"), ("+", "P1", 1, "P2"), ("+", "P2", 1, "P2"),
                                 ("+", "P1", 20, "P1")])

    def test_three_and_four_levels(self):
        same_as_hoist(self, "array M[0:3, 0:4, 0:5] at 0;\n"
                      "for i = 0 to 3 do for j = 0 to 4 do for k = 0 to 5 do s = s + M[i, j, k]; end end end")
        _, inc, _, _ = same_as_hoist(
            self,
            "array H[0:1, 0:1, 0:1, 0:2] at 0;\n"
            "for a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do "
            "s = s + H[a, b, c, d]; end end end end",
        )
        (entry,) = inc.entries
        self.assertEqual([a.level for a in entry.addresses], [1, 2, 3, 4])
        # each temporary chains from the previous level's temporary
        for outer, inner in zip(entry.addresses, entry.addresses[1:]):
            self.assertEqual(inner.init.arg1, outer.temp)

    def test_write_access(self):
        same_as_hoist(self, A + "for i = 1 to 4 do for j = 1 to 5 do A[i, j] = i * 100 + j; end end")

    def test_offsets_and_transposed_access(self):
        same_as_hoist(self, "array S[1:8, 1:8] at 300;\nfor i = 1 to 8 do for j = 1 to 8 do s = s + S[j, i]; end end")

    def test_listing_and_l2_are_not_modified(self):
        program = parse(A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        table = analyze(program)
        l2 = optimize_l2(generate(program, table, "L1"), table)
        before = (list(l2.code.quads), list(l2.code.accesses), list(l2.code.loops))
        increment_l2(l2)
        self.assertEqual((l2.code.quads, l2.code.accesses, l2.code.loops), before)

    def test_hoisting_only_behaviour_is_unchanged(self):
        l2, _, _ = incremental_of(A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end")
        first = hoist_l2(l2).code.quads
        increment_l2(l2)
        self.assertEqual(hoist_l2(l2).code.quads, first)


class IncrementalCoefficientTests(unittest.TestCase):
    def test_negative_coefficient_subtracts(self):
        _, inc, _, result = same_as_hoist(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[11 - i, j]; end end")
        (entry,) = inc.entries
        self.assertEqual(entry.addresses[0].incr.arg2, -20)
        self.assertEqual(entry.addresses[0].init.arg1, 1199 - 20)
        self.assertEqual(result.trace[0], ("R", 1180))

    def test_inner_zero_has_no_temporary(self):
        # T08: A[i, 5]: only the i level has a temporary; the j loop just reads it
        _, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, 5]; end end")
        (entry,) = inc.entries
        self.assertEqual([a.level for a in entry.addresses], [1])
        loops = {e.var: e for e in inc.code.loops}
        access = inc.code.quads[entry.access - 1]
        self.assertTrue(loops["j"].for_pos < entry.access < loops["j"].endfor_pos)
        self.assertEqual(access.arg1.index, entry.addresses[0].temp)
        self.assertEqual(entry.addresses[0].init.arg1, 984 + 20)

    def test_outer_zero_first_active_level_starts_from_k(self):
        _, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 3 do for j = 2 to 20 do s = s + A[3, j]; end end")
        (entry,) = inc.entries
        self.assertEqual([a.level for a in entry.addresses], [2])
        self.assertEqual(entry.addresses[0].init.op, ":=")  # no outer active level: a constant copy
        # the init sits before FOR j, i.e. it runs once per i iteration
        loops = {e.var: e for e in inc.code.loops}
        self.assertTrue(loops["i"].for_pos < entry.addresses[0].init_pos < loops["j"].for_pos)

    def test_middle_zero(self):
        src = ("array X[1:4, 1:4] at 0;\n"
               "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + X[i, k]; end end end")
        _, inc, _, _ = same_as_hoist(self, src)
        (entry,) = inc.entries
        self.assertEqual([a.level for a in entry.addresses], [1, 3])
        self.assertEqual(entry.addresses[1].init.arg1, entry.addresses[0].temp)

    def test_trailing_zero_temp_survives_the_inner_loop(self):
        src = ("array Z[1:4, 1:4] at 0;\n"
               "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + Z[i, j]; end end end")
        _, inc, _, _ = same_as_hoist(self, src)
        (entry,) = inc.entries
        self.assertEqual([a.level for a in entry.addresses], [1, 2])

    def test_zero_offset_init_is_still_an_addition(self):
        # j starts at 0 with D_j = 1: lo_j*D_j = 0, still `+ P1 0 P2`
        _, inc, _, _ = same_as_hoist(
            self, "array M[0:3, 0:4] at 0;\nfor i = 0 to 3 do for j = 0 to 4 do s = s + M[i, j]; end end")
        (entry,) = inc.entries
        self.assertEqual((entry.addresses[1].init.op, entry.addresses[1].init.arg2), ("+", 0))

    def test_no_active_loop_is_identical_to_l2(self):
        l2, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 3 do for j = 1 to 3 do s = s + A[3, 4]; end end")
        self.assertEqual(inc.code.quads, l2.code.quads)
        self.assertEqual(inc.entries[0].addresses, ())
        self.assertEqual(inc.addr_positions, frozenset())

    def test_no_loops_at_all(self):
        l2, inc, _, _ = same_as_hoist(self, A + "x = A[3, 4];")
        self.assertEqual(inc.code.quads, l2.code.quads)

    def test_single_loop(self):
        _, inc, _, result = same_as_hoist(self, "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end")
        (entry,) = inc.entries
        self.assertEqual(len(entry.addresses), 1)
        self.assertEqual([a for _, a in result.trace[:3]], [100, 101, 102])


class IncrementalMultipleAccessTests(unittest.TestCase):
    SRC = (
        "array A[1:4, 1:5] at 0; array B[1:4, 1:5] at 100; array C[1:4, 1:5] at 200;\n"
        "for i = 1 to 4 do for j = 1 to 5 do C[i, j] = A[i, j] + B[i, j]; end end"
    )

    def test_disjoint_temporaries(self):
        _, inc, _, _ = same_as_hoist(self, self.SRC)
        sets = [{a.temp for a in e.addresses} for e in inc.entries]
        self.assertEqual(len(sets), 3)
        self.assertTrue(all(len(s) == 2 for s in sets))
        for a, b in itertools.combinations(sets, 2):
            self.assertTrue(a.isdisjoint(b))

    def test_each_access_uses_its_own_temp(self):
        _, inc, _, _ = same_as_hoist(self, self.SRC)
        for e in inc.entries:
            self.assertEqual(inc.code.quads[e.access - 1].op in ("=[ ]", "[ ]="), True)
            cell = inc.code.quads[e.access - 1]
            index = (cell.arg1 if cell.op == "=[ ]" else cell.result).index
            self.assertEqual(index, e.addresses[-1].temp)

    def test_same_element_twice_is_not_shared(self):
        _, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j] + A[i, j]; end end")
        first, second = inc.entries
        self.assertNotEqual(first.addresses[-1].temp, second.addresses[-1].temp)

    def test_increments_precede_the_same_endfor(self):
        _, inc, _, _ = same_as_hoist(self, self.SRC)
        quads = inc.code.quads
        endfor_j = next(p for p, q in enumerate(quads, 1) if q.op == OP_ENDFOR and q.arg1 == "j")
        incs = sorted(e.addresses[-1].incr_pos for e in inc.entries)
        self.assertEqual(incs, list(range(endfor_j - 3, endfor_j)))

    def test_different_loop_sets_and_fallbacks(self):
        src = A + "for i = 1 to 3 do s = s + A[i, 1]; for j = 1 to 3 do s = s + A[i, j]; end end"
        _, inc, _, _ = same_as_hoist(self, src)
        self.assertEqual([len(e.addresses) for e in inc.entries], [1, 2])
        src = ("array A[1:4, 1:5] at 0; array V[0:99] at 500;\n"
               "for i = 1 to 4 do for j = 1 to 5 do V[i * j] = A[i, j] + A[i, 2]; end end")
        _, inc, _, _ = same_as_hoist(self, src)
        self.assertEqual([e.levels is None for e in inc.entries].count(True), 1)

    def test_fallback_window_is_untouched(self):
        src = ("array A[1:4, 1:5] at 0; array V[0:1100] at 5000;\n"
               "for i = 1 to 4 do for j = 1 to 5 do s = s + V[A[i, j] + 1]; end end")
        l2, inc, _, _ = same_as_hoist(self, src)
        self.assertEqual(inc.code.quads, l2.code.quads)

    def test_matrix_multiply(self):
        src = ("array Z[1:4, 1:4] at 32; array X[1:4, 1:4] at 0; array Y[1:4, 1:4] at 16;\n"
               "for i = 1 to 4 do for j = 1 to 4 do Z[i, j] = 0; "
               "for k = 1 to 4 do Z[i, j] = Z[i, j] + X[i, k] * Y[k, j]; end end end")
        _, inc, hoisted, result = same_as_hoist(self, src)
        self.assertEqual(hoisted.counters.loads, result.counters.loads)
        self.assertEqual(hoisted.counters.stores, result.counters.stores)
        # the only multiplication left is the body's X*Y
        self.assertEqual(result.counters.multiplications, 64)


class IncrementalZeroTripTests(unittest.TestCase):
    def test_zero_trip_inner_loop(self):
        _, _, _, result = same_as_hoist(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        self.assertEqual(result.trace, [])

    def test_zero_trip_outer_loop(self):
        _, _, _, result = same_as_hoist(self, A + "for i = 5 to 1 do for j = 1 to 3 do s = s + A[i, j]; end end")
        self.assertEqual(result.trace, [])

    def test_single_iteration_loops(self):
        same_as_hoist(self, "array U[1:1, 1:5] at 50;\nfor i = 1 to 1 do for j = 1 to 5 do s = s + U[i, j]; end end")

    def test_init_and_increment_touch_no_memory(self):
        _, inc, _, _ = same_as_hoist(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        for pos in inc.addr_positions:
            self.assertIn(inc.code.quads[pos - 1].op, (":=", "+"))

    def test_inner_init_runs_each_outer_iteration_even_with_empty_inner_loop(self):
        _, _, _, result = same_as_hoist(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        # P1 := const (1) + P2 = P1 + lo (4) + P1 += step (4), no inner increments
        self.assertEqual(result.counters.multiplications, 0)


class IncrementalStructureTests(unittest.TestCase):
    SRC = A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end"

    def test_loop_table_and_descriptors(self):
        _, inc, _, _ = same_as_hoist(self, self.SRC)
        for e in inc.code.loops:
            self.assertEqual(inc.code.quads[e.for_pos - 1].op, OP_FOR)
            self.assertEqual(inc.code.quads[e.endfor_pos - 1].op, OP_ENDFOR)
        for d in inc.code.accesses:
            self.assertIn(inc.code.quads[d.access - 1].op, ("=[ ]", "[ ]="))
            self.assertEqual(d.last + 1, d.access)
            self.assertEqual(list(d.window), [])

    def test_all_example_programs_match_hoisting(self):
        for path in sorted((ROOT / "examples").glob("*.ml")):
            with self.subTest(example=path.name):
                same_as_hoist(self, path.read_text(encoding="utf-8"))
                _, inc, _ = incremental_of(path.read_text(encoding="utf-8"))
                self.assertTrue(all(inc.code.quads[p - 1].op != "*" for p in inc.addr_positions))

    def test_listing_prints(self):
        _, inc, _ = incremental_of(self.SRC)
        self.assertIn("ENDFOR", format_listing(inc.code.quads))


# -- Phase 9.4: profitability guard --------------------------------------------------------------

from arraypeep.quad import well_formedness_errors  # noqa: E402
from arraypeep.loop_opt import GUARDED_LEVEL, format_l3_report, guarded_l3  # noqa: E402


def guarded_of(source: str):
    program = parse(source)
    table = analyze(program)
    l2 = optimize_l2(generate(program, table, "L1"), table)
    return l2, guarded_l3(l2), table


def same_as_l2(test, source: str):
    """Run L2 and the guarded listing; scalars, memory image and address trace must be equal."""
    l2, g, table = guarded_of(source)
    a = execute(l2.code.quads, table.arrays, "L2")
    b = execute(g.code.quads, table.arrays, GUARDED_LEVEL)
    test.assertEqual(real_scalars(a), real_scalars(b))
    test.assertEqual(a.memory, b.memory)
    test.assertEqual(a.trace, b.trace)
    return l2, g, a, b


class GuardCostTests(unittest.TestCase):
    def test_identity_2d_is_profitable(self):
        _, g, a, b = same_as_l2(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end")
        (e,) = g.entries
        # L2: `* i 20` and `+ T j` run per j iteration; L3: only `+ P2 1 P2`
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (2, 1, True))
        self.assertEqual(e.decision, "L3 applied (address arithmetic per innermost iteration drops from 2 to 1)")
        self.assertEqual(b.counters.multiplications, 0)
        self.assertGreater(a.counters.multiplications, 0)

    def test_t04_costs(self):
        _, g, _, _ = same_as_l2(self, A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (2, 1, True))

    def test_one_dimensional_identity_stays_at_l2(self):
        l2, g, _, _ = same_as_l2(self, "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (0, 1, False))
        self.assertEqual(e.decision, "kept at L2 (no address arithmetic to save: 0 at L2, 1 at L3 per innermost iteration)")
        self.assertEqual(g.code.quads, l2.code.quads)
        self.assertEqual(g.addr_positions, frozenset())

    def test_one_dimensional_with_offset_and_lower_bound(self):
        for src in ("array V[5:14] at 500;\nfor i = 5 to 14 do s = s + V[i]; end",
                    "array V[0:19] at 0;\nfor i = 0 to 9 do s = s + V[i + 3]; end"):
            with self.subTest(src=src):
                l2, g, _, _ = same_as_l2(self, src)
                self.assertFalse(g.entries[0].applied)
                self.assertEqual(g.code.quads, l2.code.quads)

    def test_equal_cost_is_not_strictly_lower(self):
        # V[2*i]: L2 has one multiplication, L3 one addition: equal counts, so it stays at L2
        l2, g, _, _ = same_as_l2(self, "array V[0:19] at 0;\nfor i = 0 to 9 do s = s + V[2*i]; end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (1, 1, False))
        self.assertIn("no arithmetic saved", e.decision)
        self.assertEqual(g.code.quads, l2.code.quads)

    def test_constant_address_keeps_phase8_decision(self):
        for src in (A + "x = A[3, 4];", A + "for i = 1 to 3 do for j = 1 to 3 do s = s + A[3, 4]; end end"):
            with self.subTest(src=src):
                l2, g, _, _ = same_as_l2(self, src)
                (e,) = g.entries
                self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (0, 0, False))
                self.assertEqual(e.decision, "L2 applied (constant address, no run-time arithmetic)")
                self.assertEqual(g.code.quads, l2.code.quads)

    def test_inner_zero_coefficient_is_profitable(self):
        # T08: L2 recomputes `20*i` in every j iteration; L3 has nothing in the j loop
        _, g, a, b = same_as_l2(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, 5]; end end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (1, 0, True))
        self.assertEqual(b.counters.multiplications, 0)

    def test_outer_zero_coefficient_single_term_stays(self):
        # A[3, j]: L2 address is just `j`, no arithmetic; L3 would add an increment
        l2, g, _, _ = same_as_l2(self, A + "for i = 1 to 3 do for j = 1 to 20 do s = s + A[3, j]; end end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (0, 1, False))
        self.assertEqual(g.code.quads, l2.code.quads)

    def test_negative_coefficients(self):
        _, g, _, _ = same_as_l2(self, A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[11 - i, j]; end end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (2, 1, True))
        # a negative single term with D = -1 needs a subtraction at L2? `0 - ...` is not emitted: it is `* j -1`
        _, g, _, _ = same_as_l2(self, "array V[0:9] at 0;\nfor i = 0 to 9 do s = s + V[9 - i]; end")
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (1, 1, False))

    def test_three_levels_with_middle_zero(self):
        src = ("array X[1:4, 1:4] at 0;\n"
               "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + X[i, k]; end end end")
        _, g, _, _ = same_as_l2(self, src)
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (2, 1, True))

    def test_trailing_zero_three_levels(self):
        src = ("array Z[1:4, 1:4] at 0;\n"
               "for i = 1 to 4 do for j = 1 to 4 do for k = 1 to 4 do s = s + Z[i, j]; end end end")
        _, g, _, _ = same_as_l2(self, src)
        (e,) = g.entries
        self.assertEqual((e.l2_cost, e.l3_cost, e.applied), (2, 0, True))

    def test_access_in_outer_body_uses_its_own_innermost_loop(self):
        # A[i, 1] sits in the i loop only: innermost loop = i; L2 `20*i` per i iteration, L3 `+ P1 20 P1`
        src = A + "for i = 1 to 3 do s = s + A[i, 1]; for j = 1 to 3 do s = s + A[i, j]; end end"
        _, g, _, _ = same_as_l2(self, src)
        first, second = g.entries
        self.assertEqual((first.l2_cost, first.l3_cost, first.applied), (1, 1, False))
        self.assertEqual((second.l2_cost, second.l3_cost, second.applied), (2, 1, True))


class GuardIndependenceTests(unittest.TestCase):
    MIXED = (
        "array A[1:10, 1:20] at 1000; array V[0:9] at 100;\n"
        "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j] + V[i] + A[3, 4]; end end"
    )

    def test_mixed_profitable_and_unprofitable(self):
        l2, g, _, _ = same_as_l2(self, self.MIXED)
        by_array = [(e.l2.report.array, e.applied) for e in g.entries]
        self.assertEqual(by_array, [("A", True), ("V", False), ("A", False)])

    def test_unprofitable_accesses_keep_their_l2_quadruples(self):
        l2, g, _, _ = same_as_l2(self, self.MIXED)
        _, v, c = g.entries
        # V[i] and A[3,4]: the access quadruple is the L2 one
        for ge, le in ((v, l2.entries[1]), (c, l2.entries[2])):
            self.assertEqual(g.code.quads[ge.access - 1], l2.code.quads[le.new_access - 1])

    def test_profitable_access_has_its_own_temporaries_only(self):
        _, g, _, _ = same_as_l2(self, self.MIXED)
        temps = {q.result for q in g.code.quads if q.tag == "ADDR" and isinstance(q.result, str)
                 and q.result.startswith("P")}
        self.assertEqual(len(temps), 2)  # P_i and P_j of the single profitable access only

    def test_guarded_equals_unguarded_when_everything_is_profitable(self):
        src = A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end"
        l2, g, _, _ = same_as_l2(self, src)
        self.assertEqual(g.code.quads, increment_l2(l2).code.quads)

    def test_guarded_equals_l2_when_nothing_is_profitable(self):
        l2, g, _, _ = same_as_l2(self, "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i] + V[i]; end")
        self.assertEqual(g.code.quads, l2.code.quads)
        self.assertEqual([e.applied for e in g.entries], [False, False])

    def test_fallback_next_to_profitable_access(self):
        src = ("array A[1:4, 1:5] at 0; array V[0:99] at 500;\n"
               "for i = 1 to 4 do for j = 1 to 5 do V[i * j] = A[i, j] + A[i, 2]; end end")
        l2, g, _, _ = same_as_l2(self, src)
        self.assertEqual([(e.levels is None, e.applied) for e in g.entries],
                         [(False, True), (False, True), (True, False)])
        fallback = g.entries[2]
        self.assertEqual(fallback.decision, fallback.l2.decision)
        self.assertEqual((fallback.l2_cost, fallback.l3_cost), (None, None))

    def test_fallback_window_with_nested_access_is_untouched(self):
        src = ("array A[1:4, 1:5] at 0; array V[0:1100] at 5000;\n"
               "for i = 1 to 4 do for j = 1 to 5 do s = s + V[A[i, j] + 1]; end end")
        l2, g, _, _ = same_as_l2(self, src)
        self.assertEqual(g.code.quads, l2.code.quads)

    def test_matrix_multiply_decisions(self):
        src = ("array Z[1:4, 1:4] at 32; array X[1:4, 1:4] at 0; array Y[1:4, 1:4] at 16;\n"
               "for i = 1 to 4 do for j = 1 to 4 do Z[i, j] = 0; "
               "for k = 1 to 4 do Z[i, j] = Z[i, j] + X[i, k] * Y[k, j]; end end end")
        _, g, a, b = same_as_l2(self, src)
        self.assertTrue(all(e.l3_cost is not None for e in g.entries))
        self.assertEqual(a.counters.loads, b.counters.loads)
        self.assertEqual(a.counters.stores, b.counters.stores)
        for e in g.entries:
            self.assertEqual(e.applied, e.l3_cost < e.l2_cost)


class GuardReportAndOutputTests(unittest.TestCase):
    def test_report_lists_costs_and_reasons(self):
        src = ("array A[1:10, 1:20] at 1000; array V[0:9] at 100;\n"
               "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j]; end end "
               "for k = 0 to 9 do s = s + V[k]; end")
        _, g, _ = guarded_of(src)
        text = format_l3_report(g)
        self.assertIn("access A[i, j]", text)
        self.assertIn("arithmetic   : L2 2, L3 1 per innermost iteration", text)
        self.assertIn("decision     : L3 applied (address arithmetic per innermost iteration drops from 2 to 1)", text)
        self.assertIn("arithmetic   : L2 0, L3 1 per innermost iteration", text)
        self.assertIn("decision     : kept at L2 (no address arithmetic to save: 0 at L2, 1 at L3 per innermost iteration)", text)

    def test_fallback_report_has_no_costs(self):
        _, g, _ = guarded_of("array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i * j]; end end")
        text = format_l3_report(g)
        self.assertNotIn("arithmetic   :", text)
        self.assertIn("kept at L1", text)

    def test_inputs_are_not_modified(self):
        program = parse(GuardIndependenceTests.MIXED)
        table = analyze(program)
        l2 = optimize_l2(generate(program, table, "L1"), table)
        before = (list(l2.code.quads), list(l2.code.accesses), list(l2.code.loops), list(l2.entries))
        guarded_l3(l2)
        self.assertEqual((l2.code.quads, l2.code.accesses, l2.code.loops, l2.entries), before)

    def test_unguarded_stages_are_unchanged(self):
        l2, _, _ = guarded_of(GuardIndependenceTests.MIXED)
        inc_before = increment_l2(l2).code.quads
        hoist_before = hoist_l2(l2).code.quads
        guarded_l3(l2)
        self.assertEqual(increment_l2(l2).code.quads, inc_before)
        self.assertEqual(hoist_l2(l2).code.quads, hoist_before)

    def test_listing_is_well_formed_and_level_label(self):
        _, g, _ = guarded_of(GuardIndependenceTests.MIXED)
        self.assertEqual(g.code.level, GUARDED_LEVEL)
        self.assertEqual(well_formedness_errors(g.code.quads), [])
        addr = {p for p, q in enumerate(g.code.quads, 1) if q.tag == "ADDR"}
        window = {p for d in g.code.accesses for p in d.window}
        self.assertEqual(addr, window | set(g.addr_positions))

    def test_zero_trip_and_single_iteration(self):
        same_as_l2(self, A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j]; end end")
        same_as_l2(self, A + "for i = 5 to 1 do for j = 1 to 3 do s = s + A[i, j]; end end")
        same_as_l2(self, "array U[1:1, 1:5] at 50;\nfor i = 1 to 1 do for j = 1 to 5 do s = s + U[i, j]; end end")

    def test_all_example_programs(self):
        for path in sorted((ROOT / "examples").glob("*.ml")):
            with self.subTest(example=path.name):
                same_as_l2(self, path.read_text(encoding="utf-8"))


# -- Phase 9.8: stage selection ------------------------------------------------------------------

from arraypeep.loop_opt import (  # noqa: E402
    STAGE_HOIST,
    STAGE_L2,
    STAGE_L3,
    STAGES,
    GuardedProgram,
    HoistedProgram,
    StageError,
    StageResult,
    optimize_stage,
)
from arraypeep.optimizer import L2Program  # noqa: E402

MIXED_STAGE = (
    "array A[1:10, 1:20] at 1000; array V[0:9] at 100; array W[0:99] at 2000;\n"
    "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j] + V[i] + A[3, 4] + A[i, 5] + W[i * j]; end end"
)

STAGE_SOURCES = {
    "mixed": MIXED_STAGE,
    "identity": A + "for i = 1 to 4 do for j = 1 to 5 do s = s + A[i, j]; end end",
    "t04": A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end",
    "one_d": "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end",
    "constant": A + "x = A[3, 4];",
    "negative": A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[11 - i, j]; end end",
    "zero_inner": A + "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, 5]; end end",
    "zt_inner": A + "for i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j] + A[i, 3]; end end",
    "zt_outer": A + "for i = 5 to 1 do for j = 1 to 3 do s = s + A[i, j]; end end",
    "zt_middle": ("array M[0:3, 0:4, 0:5] at 0;\n"
                  "for i = 0 to 3 do for j = 4 to 0 do for k = 0 to 5 do s = s + M[i, j, k]; end end end"),
    "fallback_nested": ("array A[1:4, 1:5] at 0; array V[0:1100] at 5000;\n"
                        "for i = 1 to 4 do for j = 1 to 5 do s = s + V[A[i, j] + 1] + A[i, j]; end end"),
}


def stages_of(source: str):
    program = parse(source)
    table = analyze(program)
    l2 = optimize_l2(generate(program, table, "L1"), table)
    return l2, table


class StageSelectionTests(unittest.TestCase):
    def test_stage_names_are_exactly_three(self):
        self.assertEqual(STAGES, ("L2", "hoist", "L3"))
        self.assertEqual((STAGE_L2, STAGE_HOIST, STAGE_L3), STAGES)

    def test_each_stage_selects_its_implementation_and_type(self):
        l2, _ = stages_of(MIXED_STAGE)
        expected = {STAGE_L2: L2Program, STAGE_HOIST: HoistedProgram, STAGE_L3: GuardedProgram}
        for stage, kind in expected.items():
            with self.subTest(stage=stage):
                result = optimize_stage(l2, stage)
                self.assertIsInstance(result, StageResult)
                self.assertEqual(result.stage, stage)
                self.assertIs(type(result.program), kind)

    def test_l2_stage_is_the_phase8_program_unchanged(self):
        l2, _ = stages_of(MIXED_STAGE)
        before = (list(l2.code.quads), list(l2.code.accesses), list(l2.code.loops), list(l2.entries))
        result = optimize_stage(l2, STAGE_L2)
        self.assertIs(result.program, l2)
        self.assertIs(result.code, l2.code)
        self.assertEqual((l2.code.quads, l2.code.accesses, l2.code.loops, l2.entries), before)
        self.assertEqual(result.code.level, "L2")

    def test_hoist_stage_matches_hoist_l2(self):
        for name, src in STAGE_SOURCES.items():
            with self.subTest(name=name):
                l2, _ = stages_of(src)
                direct = hoist_l2(l2)
                result = optimize_stage(l2, STAGE_HOIST)
                self.assertEqual(result.code.quads, direct.code.quads)
                self.assertEqual(result.code.accesses, direct.code.accesses)
                self.assertEqual(result.code.loops, direct.code.loops)
                self.assertEqual(result.code.level, HOISTED_LEVEL)
                self.assertEqual(result.program.hoisted_positions, direct.hoisted_positions)
                self.assertEqual([e.decision for e in result.entries], [e.decision for e in direct.entries])

    def test_l3_stage_matches_guarded_l3(self):
        for name, src in STAGE_SOURCES.items():
            with self.subTest(name=name):
                l2, _ = stages_of(src)
                direct = guarded_l3(l2)
                result = optimize_stage(l2, STAGE_L3)
                self.assertEqual(result.code.quads, direct.code.quads)
                self.assertEqual(result.code.accesses, direct.code.accesses)
                self.assertEqual(result.code.loops, direct.code.loops)
                self.assertEqual(result.code.level, GUARDED_LEVEL)
                self.assertEqual(result.program.addr_positions, direct.addr_positions)
                self.assertEqual(
                    [(e.l2_cost, e.l3_cost, e.applied, e.decision) for e in result.entries],
                    [(e.l2_cost, e.l3_cost, e.applied, e.decision) for e in direct.entries],
                )
                self.assertEqual(format_l3_report(result.program), format_l3_report(direct))

    def test_selected_stage_is_explicit_in_the_result(self):
        l2, _ = stages_of(MIXED_STAGE)
        self.assertEqual([optimize_stage(l2, s).stage for s in STAGES], ["L2", "hoist", "L3"])

    def test_result_is_frozen(self):
        l2, _ = stages_of(MIXED_STAGE)
        result = optimize_stage(l2, STAGE_L2)
        with self.assertRaises(Exception):
            result.stage = "L3"  # type: ignore[misc]

    def test_entries_have_one_per_access_at_every_stage(self):
        l2, _ = stages_of(MIXED_STAGE)
        for stage in STAGES:
            with self.subTest(stage=stage):
                result = optimize_stage(l2, stage)
                self.assertEqual(len(result.entries), len(l2.entries))
                sources = [(e if stage == STAGE_L2 else e.l2).report.source for e in result.entries]
                self.assertEqual(sources, [e.report.source for e in l2.entries])


class StageErrorTests(unittest.TestCase):
    def setUp(self):
        self.l2, _ = stages_of(STAGE_SOURCES["identity"])

    def test_unsupported_names_are_rejected(self):
        for bad in ("L0", "L1", "l2", "L3 ", "", "hoisting", "HOIST", "L3-incr", "L3-hoist", "incremental", "all"):
            with self.subTest(stage=bad):
                with self.assertRaises(StageError) as ctx:
                    optimize_stage(self.l2, bad)
                self.assertEqual(str(ctx.exception),
                                 f"unknown optimization stage {bad!r}; expected one of: L2, hoist, L3")

    def test_non_string_stages_are_rejected_the_same_way(self):
        for bad in (None, 2, 3, True, ("L2",), ["L3"], b"L2"):
            with self.subTest(stage=bad):
                with self.assertRaises(StageError):
                    optimize_stage(self.l2, bad)  # type: ignore[arg-type]

    def test_error_is_a_project_error(self):
        from arraypeep.symbol_table import ArrayPeepError
        self.assertTrue(issubclass(StageError, ArrayPeepError))

    def test_error_does_not_run_any_stage(self):
        before = (list(self.l2.code.quads), list(self.l2.entries))
        with self.assertRaises(StageError):
            optimize_stage(self.l2, "nonsense")
        self.assertEqual((self.l2.code.quads, self.l2.entries), before)


class StageEquivalenceTests(unittest.TestCase):
    def test_all_stages_equal_l2_behaviour(self):
        for name, src in STAGE_SOURCES.items():
            l2, table = stages_of(src)
            base = execute(l2.code.quads, table.arrays, "L2")
            for stage in STAGES:
                with self.subTest(name=name, stage=stage):
                    result = optimize_stage(l2, stage)
                    run = execute(result.code.quads, table.arrays, result.code.level)
                    self.assertEqual(real_scalars(run), real_scalars(base))
                    self.assertEqual(run.memory, base.memory)
                    self.assertEqual(run.trace, base.trace)
                    self.assertEqual(run.counters.loads, base.counters.loads)
                    self.assertEqual(run.counters.stores, base.counters.stores)

    def test_zero_trip_pre_headers_touch_no_memory(self):
        for name in ("zt_inner", "zt_outer", "zt_middle"):
            l2, table = stages_of(STAGE_SOURCES[name])
            for stage in (STAGE_HOIST, STAGE_L3):
                with self.subTest(name=name, stage=stage):
                    result = optimize_stage(l2, stage)
                    run = execute(result.code.quads, table.arrays, stage)
                    self.assertEqual(run.trace, [])
                    generated = (result.program.hoisted_positions if stage == STAGE_HOIST
                                 else result.program.addr_positions)
                    for pos in generated:
                        self.assertIn(result.code.quads[pos - 1].op, (":=", "+", "*"))

    def test_mixed_program_per_stage(self):
        l2, _ = stages_of(MIXED_STAGE)
        by_stage = {s: optimize_stage(l2, s) for s in STAGES}
        # L2 keeps every access as in Phase 8
        self.assertEqual([e.decision for e in by_stage[STAGE_L2].entries], [e.decision for e in l2.entries])
        # hoisting touches every planned access, the fallback is copied
        self.assertEqual([e.levels is None for e in by_stage[STAGE_HOIST].entries],
                         [e.plan is None for e in l2.entries])
        # full L3 decides per access: A[i,j] and A[i,5] profitable; V[i], A[3,4] kept; W[i*j] fallback
        decisions = [(e.l2.report.source, e.applied) for e in by_stage[STAGE_L3].entries]
        self.assertEqual(decisions, [("A[i, j]", True), ("V[i]", False), ("A[3, 4]", False),
                                     ("A[i, 5]", True), ("W[i * j]", False)])

    def test_fallback_and_nested_windows_untouched_at_every_stage(self):
        l2, _ = stages_of(STAGE_SOURCES["fallback_nested"])
        for stage in (STAGE_HOIST, STAGE_L3):
            with self.subTest(stage=stage):
                result = optimize_stage(l2, stage)
                fallback = next(e for e in result.entries if (e.levels is None))
                self.assertEqual(fallback.decision, fallback.l2.decision)

    def test_direct_functions_unchanged_by_the_switch(self):
        l2, _ = stages_of(MIXED_STAGE)
        before = (hoist_l2(l2).code.quads, increment_l2(l2).code.quads, guarded_l3(l2).code.quads)
        for stage in STAGES:
            optimize_stage(l2, stage)
        after = (hoist_l2(l2).code.quads, increment_l2(l2).code.quads, guarded_l3(l2).code.quads)
        self.assertEqual(before, after)

    def test_all_example_programs(self):
        for path in sorted((ROOT / "examples").glob("*.ml")):
            l2, table = stages_of(path.read_text(encoding="utf-8"))
            base = execute(l2.code.quads, table.arrays, "L2")
            for stage in STAGES:
                with self.subTest(example=path.name, stage=stage):
                    result = optimize_stage(l2, stage)
                    run = execute(result.code.quads, table.arrays, result.code.level)
                    self.assertEqual((run.memory, run.trace), (base.memory, base.trace))