"""Tests for arraypeep.interpreter and arraypeep.metrics (roadmap Phase 6).

Expected values are hand-calculated from docs/quad_spec.md (Sections 3.1, 4, 8,
10) and docs/hand_examples.md; the plain-Python computations in this file do
not use the code under test.
"""
import pathlib
import subprocess
import sys
import unittest

from arraypeep.codegen import generate
from arraypeep.interpreter import (
    ExecutionError,
    InternalError,
    RunResult,
    RuntimeAccessError,
    StepLimitError,
    execute,
    init_value,
    initial_memory,
    match_loops,
    memory_checksum,
)
from arraypeep.metrics import Counters
from arraypeep.parser import parse
from arraypeep.quad import Indexed, Quad
from arraypeep.semantic import analyze
from arraypeep.symbol_table import ArrayInfo, ArrayPeepError

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))
LEVELS = ("L0", "L1")


def run(source: str, level: str = "L1", **kwargs) -> RunResult:
    program = parse(source)
    table = analyze(program)
    return execute(generate(program, table, level).quads, table.arrays, level, **kwargs)


def both(source: str) -> dict[str, RunResult]:
    return {level: run(source, level) for level in LEVELS}


# -- machine state ------------------------------------------------------------------------


class InitialMemoryTests(unittest.TestCase):
    def test_pattern_values_of_the_specification(self) -> None:
        self.assertEqual([init_value(x) for x in (1000, 1001, 0, -1)], [737, 768, 7, 985])

    def test_pattern_is_a_mathematical_modulo_and_injective_on_1009_addresses(self) -> None:
        for x in range(-3000, 3000):
            self.assertTrue(0 <= init_value(x) <= 1008)
        for start in (-700, 0, 1000):
            self.assertEqual(len({init_value(x) for x in range(start, start + 1009)}), 1009)

    def test_memory_covers_exactly_the_declared_regions(self) -> None:
        arrays = [ArrayInfo("V", [(0, 9)], 100), ArrayInfo("A", [(1, 2), (1, 3)], -3)]
        memory = initial_memory(arrays)
        self.assertEqual(sorted(memory), list(range(-3, 3)) + list(range(100, 110)))
        self.assertTrue(all(v == init_value(a) for a, v in memory.items()))

    def test_no_arrays_gives_empty_memory(self) -> None:
        self.assertEqual(initial_memory([]), {})


# -- scalar programs against hand-calculated results ------------------------------------------


class ScalarProgramTests(unittest.TestCase):
    def test_roadmap_loop_and_its_counts(self) -> None:
        r = run("for i = 1 to 3 do s = s + i * 2; end")
        self.assertEqual(r.scalars, {"s": 12})  # 2 + 4 + 6
        c = r.counters
        self.assertEqual((c.static, c.total), (5, 13))  # 1 + 3*(3 + 1)
        self.assertEqual(c.by_op, {"FOR": 1, "*": 3, "+": 3, ":=": 3, "ENDFOR": 3})
        self.assertEqual((c.multiplications, c.add_sub, c.loads, c.stores), (3, 3, 0, 0))
        self.assertEqual(r.trace, [])
        self.assertEqual(r.memory, {})

    def test_expressions(self) -> None:
        r = run("x = (2 + 3) * 4 - -5; a = 7; b = a * a - 3 * (a - 1); n = -(2 * 3);")
        self.assertEqual(r.scalars, {"a": 7, "b": 31, "n": -6, "x": 25})

    def test_loop_sums_and_unassigned_scalars(self) -> None:
        r = run("for i = 1 to 10 do s = s + i; end t = u + 1;")
        self.assertEqual(r.scalars, {"s": 55, "t": 1, "u": 0})  # never-assigned u is 0

    def test_temporaries_and_loop_variables_are_not_scalars(self) -> None:
        r = run("for i = 1 to 3 do s = s + i * 2; end")
        self.assertEqual(set(r.scalars), {"s"})

    def test_unbounded_integers(self) -> None:
        r = run("x = 1; for i = 1 to 70 do x = x * 2; end")
        self.assertEqual(r.scalars["x"], 2 ** 70)

    def test_zero_trip_loop_executes_only_its_for(self) -> None:
        r = run("for i = 5 to 1 do s = 1; end")
        self.assertEqual((r.scalars, r.counters.total, r.counters.by_op), ({"s": 0}, 1, {"FOR": 1}))

    def test_nested_zero_trip_loop_counts_its_for_per_outer_iteration(self) -> None:
        r = run("for i = 1 to 4 do for j = 5 to 1 do s = 1; end end")
        self.assertEqual(r.counters.total, 1 + 4 * 2)  # outer FOR; per iteration inner FOR + outer ENDFOR
        self.assertEqual(r.counters.by_op, {"FOR": 5, "ENDFOR": 4})
        self.assertEqual(r.scalars, {"s": 0})

    def test_single_iteration_loop(self) -> None:
        r = run("for i = 3 to 3 do s = s + i; end")
        self.assertEqual((r.scalars, r.counters.total), ({"s": 3}, 1 + 1 * (2 + 1)))

    def test_negative_bounds(self) -> None:
        self.assertEqual(run("for i = -2 to 2 do s = s + i * i; end").scalars, {"s": 10})

    def test_nested_loop_values(self) -> None:
        r = run("for i = 1 to 3 do for j = 1 to 4 do s = s + i * j; end end")
        self.assertEqual(r.scalars, {"s": 6 * 10})

    def test_sanity_formula_one_plus_n_times_k_plus_one(self) -> None:
        # (N, source, k): a single loop with N iterations and a body of k quadruples
        for n, body, k in [(1, "s = 1;", 1), (3, "s = s + i * 2;", 3), (10, "s = s + i;", 2), (7, "s = i * 2 + i * 3;", 4)]:
            with self.subTest(n=n, body=body):
                r = run(f"for i = 1 to {n} do {body} end")
                self.assertEqual(r.counters.static, k + 2)
                self.assertEqual(r.counters.total, 1 + n * (k + 1))

    def test_per_opcode_and_per_tag_counts_sum_to_the_total(self) -> None:
        for source in ["for i = 1 to 3 do s = s + i * 2; end", "s = 1;",
                       "for i = 1 to 4 do for j = 5 to 1 do s = 1; end end"]:
            c = run(source).counters
            self.assertEqual(sum(c.by_op.values()), c.total)
            self.assertEqual(sum(c.by_tag.values()), c.total)
            self.assertEqual(sum(c.by_op_tag.values()), c.total)

    def test_empty_program(self) -> None:
        r = execute([], [])
        self.assertEqual((r.scalars, r.memory, r.trace, r.counters.total), ({}, {}, [], 0))


# -- array programs -----------------------------------------------------------------------


class TraversalTests(unittest.TestCase):
    SRC = ("array A[1:10, 1:20] at 1000;\ns = 0;\n"
           "for i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end")

    def test_full_traversal_visits_1000_to_1199_at_both_levels(self) -> None:
        for level, r in both(self.SRC).items():
            with self.subTest(level=level):
                self.assertEqual(r.trace, [("R", a) for a in range(1000, 1200)])

    def test_value_is_the_sum_of_the_initial_pattern(self) -> None:
        expected = sum((31 * x + 7) % 1009 for x in range(1000, 1200))
        self.assertEqual(expected, 101387)  # hand_examples.md, Section 6
        for r in both(self.SRC).values():
            self.assertEqual(r.scalars, {"s": 101387})
            self.assertEqual(r.memory, initial_memory([ArrayInfo("A", [(1, 10), (1, 20)], 1000)]))

    def test_hand_calculated_dynamic_counts(self) -> None:
        # L1: per j-iteration 2 ADDR + load + '+' + ':=' + ENDFOR = 6; L0: 5 ADDR, so 9.
        r = both(self.SRC)
        self.assertEqual(r["L1"].counters.total, 1 + 1 + 10 * (1 + 20 * 6 + 1))  # s=0, FOR i, ...
        self.assertEqual(r["L0"].counters.total, 1 + 1 + 10 * (1 + 20 * 9 + 1))
        self.assertEqual((r["L1"].counters.addr, r["L1"].counters.addr_multiplications, r["L1"].counters.addr_add_sub), (400, 200, 200))
        self.assertEqual((r["L0"].counters.addr, r["L0"].counters.addr_multiplications, r["L0"].counters.addr_add_sub), (1000, 200, 800))
        for c in (r["L0"].counters, r["L1"].counters):
            self.assertEqual((c.loads, c.stores), (200, 0))

    def test_second_example_counts_of_the_specification(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = s + A[2 * i + 1, j - 1]; end end"
        r = both(src)
        self.assertEqual(r["L0"].trace, r["L1"].trace)
        self.assertEqual(r["L0"].trace[0], ("R", 1040))
        self.assertEqual(r["L0"].trace[-1], ("R", 1178))
        self.assertEqual(len(r["L0"].trace), 76)
        # quad_spec.md 7.2: L0 152 mult / 456 add-sub, L1 152 / 228 (address arithmetic only)
        self.assertEqual((r["L0"].counters.addr_multiplications, r["L0"].counters.addr_add_sub), (152, 456))
        self.assertEqual((r["L1"].counters.addr_multiplications, r["L1"].counters.addr_add_sub), (152, 228))

    def test_writes_then_reads_one_dimension(self) -> None:
        src = "array V[5:14] at 500;\nfor i = 5 to 14 do V[i] = 2 * i; end s = 0;\nfor i = 5 to 14 do s = s + V[i]; end"
        for level, r in both(src).items():
            with self.subTest(level=level):
                self.assertEqual(r.scalars, {"s": 190})
                self.assertEqual(r.memory, {500 + k: 2 * (5 + k) for k in range(10)})
                self.assertEqual(r.trace, [("W", a) for a in range(500, 510)] + [("R", a) for a in range(500, 510)])
                self.assertEqual((r.counters.loads, r.counters.stores), (10, 10))

    def test_three_dimensional_and_four_dimensional_traces(self) -> None:
        r3 = both("array M[0:3, 0:4, 0:5] at 0;\nfor i = 0 to 3 do for j = 0 to 4 do for k = 0 to 5 do s = s + M[i, j, k]; end end end")
        for r in r3.values():
            self.assertEqual(r.trace, [("R", a) for a in range(120)])
        r4 = both("array H[0:1, 0:1, 0:1, 0:2] at 0;\nfor a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do s = s + H[a, b, c, d]; end end end end")
        for r in r4.values():
            self.assertEqual(r.trace, [("R", a) for a in range(24)])

    def test_transposed_access_is_non_sequential(self) -> None:
        src = "array S[1:8, 1:8] at 300;\nfor i = 1 to 8 do for j = 1 to 8 do s = s + S[j, i]; end end"
        for r in both(src).values():
            self.assertEqual([a for _, a in r.trace[:3]], [300, 308, 316])

    def test_matrix_multiply_against_plain_python(self) -> None:
        src = ("array Z[1:4, 1:4] at 32;\narray X[1:4, 1:4] at 0;\narray Y[1:4, 1:4] at 16;\n"
               "for i = 1 to 4 do for k = 1 to 4 do X[i, k] = i * 10 + k; Y[i, k] = i - 2 * k; end end\n"
               "for i = 1 to 4 do for j = 1 to 4 do Z[i, j] = 0; for k = 1 to 4 do "
               "Z[i, j] = Z[i, j] + X[i, k] * Y[k, j]; end end end")
        X = {(i, k): i * 10 + k for i in range(1, 5) for k in range(1, 5)}
        Y = {(k, j): k - 2 * j for k in range(1, 5) for j in range(1, 5)}
        for level, r in both(src).items():
            with self.subTest(level=level):
                for i in range(1, 5):
                    for j in range(1, 5):
                        self.assertEqual(r.memory[27 + 4 * i + j], sum(X[i, k] * Y[k, j] for k in range(1, 5)))
                self.assertEqual(r.memory[0 + (-5 + 4 * 2 + 3)], X[2, 3])  # X[2,3] at -5 + 4*i + k

    def test_l0_and_l1_agree_on_every_example(self) -> None:
        self.assertEqual(len(EXAMPLES), 5)
        for path in EXAMPLES:
            r = both(path.read_text(encoding="utf-8"))
            with self.subTest(example=path.name):
                self.assertEqual(r["L0"].scalars, r["L1"].scalars)
                self.assertEqual(r["L0"].memory, r["L1"].memory)
                self.assertEqual(r["L0"].checksum, r["L1"].checksum)
                self.assertEqual(r["L0"].trace, r["L1"].trace)
                self.assertTrue(r["L0"].trace)
                self.assertEqual((r["L0"].counters.loads, r["L0"].counters.stores),
                                 (r["L1"].counters.loads, r["L1"].counters.stores))
                self.assertGreater(r["L0"].counters.addr, r["L1"].counters.addr)

    def test_counter_totals_on_every_example(self) -> None:
        for path in EXAMPLES:
            for level, r in both(path.read_text(encoding="utf-8")).items():
                c = r.counters
                with self.subTest(example=path.name, level=level):
                    self.assertEqual(sum(c.by_op.values()), c.total)
                    self.assertEqual(c.addr + c.body, c.total)
                    self.assertEqual(c.loads, sum(1 for k, _ in r.trace if k == "R"))
                    self.assertEqual(c.stores, sum(1 for k, _ in r.trace if k == "W"))
                    self.assertEqual(c.add_sub, c.additions + c.subtractions)

    def test_nested_array_subscript_trace(self) -> None:
        src = ("array V[0:9] at 0;\narray W[0:9] at 100;\n"
               "for i = 0 to 9 do W[i] = 9 - i; end\nfor i = 0 to 3 do s = s + V[W[i]]; end")
        want = [("W", 100 + i) for i in range(10)] + [x for i in range(4) for x in (("R", 100 + i), ("R", 9 - i))]
        for r in both(src).values():
            self.assertEqual(r.trace, want)
            self.assertEqual(r.scalars, {"s": sum(init_value(9 - i) for i in range(4))})


# -- run-time safety ---------------------------------------------------------------------


class RuntimeSafetyTests(unittest.TestCase):
    DECL = "array V[0:9] at 100;\n"

    def test_out_of_range_read_names_the_quadruple(self) -> None:
        with self.assertRaises(RuntimeAccessError) as ctx:
            run(self.DECL + "s = V[12];", "L1")
        e = ctx.exception
        self.assertEqual(e.position, 1)
        self.assertEqual(
            e.message,
            "runtime error: level L1, quadruple 1 [=[ ] 100[12] - T1]: effective address 112 is outside every declared array",
        )
        self.assertIsInstance(e, ExecutionError)
        self.assertIsInstance(e, ArrayPeepError)

    def test_l0_names_its_own_quadruple_and_address(self) -> None:
        with self.assertRaises(RuntimeAccessError) as ctx:
            run(self.DECL + "s = V[12];", "L0")
        self.assertEqual(ctx.exception.position, 3)  # after the two address quadruples
        self.assertIn("quadruple 3 [=[ ] 0[T2] - T3]", ctx.exception.message)
        self.assertIn("effective address 112", ctx.exception.message)

    def test_boundaries_of_the_region(self) -> None:
        for level in LEVELS:
            self.assertEqual(run(self.DECL + "s = V[0] + V[9];", level).scalars, {"s": init_value(100) + init_value(109)})
            for bad, address in (("V[10]", 110), ("V[0 - 1]", 99)):
                with self.subTest(level=level, access=bad):
                    with self.assertRaises(RuntimeAccessError) as ctx:
                        run(self.DECL + f"s = {bad};", level)
                    self.assertIn(f"effective address {address} is outside", ctx.exception.message)

    def test_out_of_range_write_names_the_quadruple(self) -> None:
        with self.assertRaises(RuntimeAccessError) as ctx:
            run(self.DECL + "V[10] = 5;", "L1")
        self.assertIn("quadruple 1 [[ ]= 5 - 100[10]]", ctx.exception.message)

    def test_error_in_a_loop_stops_at_the_first_bad_access(self) -> None:
        with self.assertRaises(RuntimeAccessError) as ctx:
            run(self.DECL + "for i = 0 to 20 do s = s + V[i]; end", "L1")
        self.assertIn("effective address 110", ctx.exception.message)

    def test_gap_between_arrays_is_invalid_but_adjacent_arrays_are_fine(self) -> None:
        src = "array A[0:4] at 0;\narray B[0:4] at 5;\narray C[0:4] at 20;\n"
        self.assertEqual(run(src + "s = A[4] + B[0];").scalars, {"s": init_value(4) + init_value(5)})
        with self.assertRaises(RuntimeAccessError):
            run(src + "s = B[10];")  # address 10 lies in the gap

    def test_two_dimensional_subscript_out_of_range_but_inside_the_array_is_accepted(self) -> None:
        # quad_spec.md 4.3: the check is on the address; A[1, 25] has the address of A[2, 5]
        for level in LEVELS:
            r = run("array A[1:10, 1:20] at 1000;\ns = A[1, 25];", level)
            self.assertEqual(r.trace, [("R", 1024)])

    def test_step_limit(self) -> None:
        src = "for i = 1 to 3 do s = s + i * 2; end"  # 13 dynamic quadruples
        self.assertEqual(run(src, step_limit=13).counters.total, 13)
        with self.assertRaises(StepLimitError) as ctx:
            run(src, step_limit=12)
        self.assertEqual(ctx.exception.message, "runtime error: level L1, step limit of 12 executed quadruples exceeded")

    def test_ill_formed_listings_are_internal_errors(self) -> None:
        cases = [
            [Quad("+", "T1", 1, "T2")],  # T1 read before assigned
            [Quad("FOR", "i", 1, 3)],  # no ENDFOR
            [Quad("ENDFOR", "i")],  # no FOR
            [Quad("FOR", "i", 1, 2), Quad(":=", 1, None, "i"), Quad("ENDFOR", "i")],  # assigns the loop variable
        ]
        for quads in cases:
            with self.subTest(quads=quads):
                with self.assertRaises(InternalError) as ctx:
                    execute(quads, [])
                self.assertTrue(ctx.exception.message.startswith("internal error: "))

    def test_hand_built_listing_with_indexed_operands(self) -> None:
        info = ArrayInfo("A", [(1, 3)], 10)  # conspart 9
        quads = [Quad("[ ]=", 42, None, Indexed(9, 2)), Quad("=[ ]", Indexed(9, 2), None, "T1"), Quad(":=", "T1", None, "x")]
        r = execute(quads, [info])
        self.assertEqual((r.scalars, r.trace, r.memory[11]), ({"x": 42}, [("W", 11), ("R", 11)], 42))


# -- loop matching, checksum, result text, independence ---------------------------------------------


class SupportTests(unittest.TestCase):
    def test_match_loops(self) -> None:
        quads = [Quad("FOR", "i", 1, 2), Quad("FOR", "j", 1, 2), Quad("ENDFOR", "j"), Quad("FOR", "k", 1, 2),
                 Quad("ENDFOR", "k"), Quad("ENDFOR", "i")]
        m = match_loops(quads)
        self.assertEqual({k: v for k, v in m.items() if k < v}, {0: 5, 1: 2, 3: 4})
        self.assertEqual(m[5], 0)

    def test_checksum_is_deterministic_order_independent_and_sensitive(self) -> None:
        a = {1: 5, 2: 6, 3: 7}
        b = {3: 7, 1: 5, 2: 6}
        self.assertEqual(memory_checksum(a), memory_checksum(b))
        self.assertRegex(memory_checksum(a), r"^[0-9a-f]{16}$")
        self.assertNotEqual(memory_checksum(a), memory_checksum({1: 5, 2: 6, 3: 8}))
        self.assertNotEqual(memory_checksum(a), memory_checksum({1: 5, 2: 7, 3: 6}))
        self.assertEqual(memory_checksum({}), memory_checksum({}))

    def test_runs_are_reproducible(self) -> None:
        src = "array A[1:3] at 0;\nfor i = 1 to 3 do A[i] = A[i] + i; end"
        r1, r2 = run(src), run(src)
        self.assertEqual((r1.scalars, r1.memory, r1.trace, r1.checksum), (r2.scalars, r2.memory, r2.trace, r2.checksum))
        self.assertEqual(r1.counters, r2.counters)

    def test_formatted_result(self) -> None:
        text = run("array A[1:3] at 0;\ns = A[2];").format()
        self.assertIn("scalars      s = ", text)
        self.assertIn("checksum", text)
        self.assertIn("dynamic quadruples", text)
        self.assertNotIn("\n\n", text)

    def test_counters_record_and_derived_properties(self) -> None:
        c = Counters(static=3)
        for op, tag in [("*", "ADDR"), ("+", "ADDR"), ("-", "BODY"), ("=[ ]", "BODY"), ("[ ]=", "BODY")]:
            c.record(op, tag)
        self.assertEqual((c.total, c.multiplications, c.additions, c.subtractions, c.add_sub), (5, 1, 1, 1, 2))
        self.assertEqual((c.loads, c.stores, c.addr, c.body, c.addr_multiplications, c.addr_add_sub), (1, 1, 2, 3, 1, 1))

    def test_modules_do_not_load_the_optimiser_or_the_front_end(self) -> None:
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "import arraypeep.interpreter, arraypeep.metrics;"
                "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True).stdout
        for forbidden in ("lexer", "parser", "semantic", "codegen", "optimizer", "driver", "linear_analysis",
                          "precompute", "loop_opt", "report"):
            self.assertNotIn(f"arraypeep.{forbidden}'", out)


if __name__ == "__main__":
    unittest.main()
