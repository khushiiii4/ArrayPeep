"""End-to-end tests (Phase 11.3 and 11.4): the T01-T18 matrix of roadmap 5.3 and extra edge cases.

Every case is a MiniLoop file in ``tests/cases/`` with an expected-results file
(``T<nn>.expected.json``: K and D coefficients, optimization decision, trace
summary, scalar rule). Each program is run at L0, L1, L2, hoist and L3 through
the 11.2 harness (``tests/equivalence.py``), which compares scalars, memory and
the address trace of every level with L0 and the trace with the independent
oracle (``tests/oracle.py``). On top of that this module checks:

* the expected K, D and decision of every access against the L3 optimization report;
* the trace summary of the expected file against the oracle trace;
* the final scalars and memory image against values computed here in plain
  Python from the oracle's initial-memory formula (including Z = X*Y for T11).

Decision kinds in the expected files: ``L3`` (L3 applied), ``kept-at-L2``
(guard: no arithmetic saved), ``constant`` (constant address),
``fallback`` (non-linear subscript, kept at L1) and ``inside-fallback-window``.
"""
import json
import pathlib
import sys
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:  # also works for `python -m unittest tests.test_end_to_end`
    sys.path.insert(0, str(TESTS_DIR))

import equivalence as eq  # noqa: E402
import oracle  # noqa: E402
from oracle import Array, Loop, Statement, assign, linear, read, write  # noqa: E402

CASES_DIR = TESTS_DIR / "cases"
CASE_IDS = [f"T{n:02d}" for n in range(1, 19)]

A = Array("A", (1, 1), (10, 20), 1000)  # A[1:10, 1:20] at 1000


def loops(*ranges):
    """``loops(("i", 1, 4), ("j", 2, 20))(body)`` is a perfect nest of Loops around ``body``."""

    def build(body):
        for var, lo, hi in reversed(ranges):
            body = [Loop(var, lo, hi, body)]
        return body

    return build


def reads_stmt(*accesses):
    return [Statement(list(accesses))]


# -- oracle descriptions of T01-T18 (plain data; the subscript functions come from roadmap 5.3) ---------

def _t10():
    arrays = [Array("A", (1, 1), (4, 5), 0), Array("B", (1, 1), (4, 5), 100), Array("C", (1, 1), (4, 5), 200)]
    body = loops(("i", 1, 4), ("j", 1, 5))(
        [assign(write("C", "i", "j"), read("A", "i", "j"), read("B", "i", "j"))])
    return arrays, body


def _t11():
    arrays = [Array("X", (1, 1), (4, 4), 0), Array("Y", (1, 1), (4, 4), 16), Array("Z", (1, 1), (4, 4), 32)]
    update = assign(write("Z", "i", "j"), read("Z", "i", "j"), read("X", "i", "k"), read("Y", "k", "j"))
    body = loops(("i", 1, 4), ("j", 1, 4))([assign(write("Z", "i", "j")), Loop("k", 1, 4, [update])])
    return arrays, body


def _t13():
    arrays = [Array("W", (0,), (9,), 0), Array("V", (0,), (9,), 10)]
    body = [
        Loop("i", 0, 9, [assign(write("W", "i"))]),
        # s = s + V[W[i]]: the read of W[i] in the subscript, then the read of V[9 - i] (W[i] holds 9 - i)
        Loop("i", 0, 9, [Statement([read("W", "i"), read("V", lambda env: 9 - env["i"])])]),
    ]
    return arrays, body


def _one(array, ranges, *subscripts):
    return [array], loops(*ranges)(reads_stmt(read(array.name, *subscripts)))


ORACLE = {
    "T01": lambda: _one(Array("V0", (0,), (9,), 100), [("i", 0, 9)], "i"),
    "T02": lambda: _one(Array("V5", (5,), (14,), 500), [("i", 5, 14)], "i"),
    "T03": lambda: _one(A, [("i", 1, 10), ("j", 1, 20)], "i", "j"),
    "T04": lambda: _one(A, [("i", 1, 4), ("j", 2, 20)], linear(1, i=2), linear(-1, j=1)),
    "T05": lambda: _one(Array("M", (0, 0, 0), (3, 4, 5), 0), [("i", 0, 3), ("j", 0, 4), ("k", 0, 5)], "i", "j", "k"),
    "T06": lambda: _one(Array("N", (-5, -3), (5, 3), 200), [("i", -5, 5), ("j", -3, 3)], "i", "j"),
    "T07": lambda: ([A], reads_stmt(read("A", 3, 4))),
    "T08": lambda: _one(A, [("i", 1, 10), ("j", 1, 3)], "i", 5),
    "T09": lambda: _one(Array("S", (1, 1), (8, 8), 300), [("i", 1, 8), ("j", 1, 8)], "j", "i"),
    "T10": _t10,
    "T11": _t11,
    "T12": lambda: _one(Array("V", (0,), (99,), 0), [("i", 0, 9), ("j", 0, 9)], lambda env: env["i"] * env["j"]),
    "T13": _t13,
    "T14": lambda: _one(A, [("i", 1, 5)], lambda env: env["i"] + 2, 1),  # k = 2 is assigned before the loop
    "T15": lambda: _one(A, [("i", 5, 1)], "i", 1),
    "T16": lambda: _one(Array("U", (1, 1), (1, 5), 50), [("i", 1, 1), ("j", 1, 5)], "i", "j"),
    "T17": lambda: _one(Array("H", (0, 0, 0, 0), (1, 1, 1, 2), 0),
                        [("i", 0, 1), ("j", 0, 1), ("k", 0, 1), ("m", 0, 2)], "i", "j", "k", "m"),
    "T18": lambda: _one(A, [("i", 1, 10), ("j", 1, 20)], linear(11, i=-1), "j"),
}


# -- expected final memory (computed here, independently of the compiler) ------------------------------

def memory_t10(memory, arrays):
    for n in range(20):  # C[i,j] = A[i,j] + B[i,j]; element n of each array is at base + n
        memory[200 + n] = oracle.init_value(n) + oracle.init_value(100 + n)


def memory_t11(memory, arrays):
    """Z = X * Y with X and Y holding the initial pattern (X at 0..15, Y at 16..31, Z at 32..47)."""
    x = lambda i, k: oracle.init_value(0 + 4 * (i - 1) + (k - 1))
    y = lambda k, j: oracle.init_value(16 + 4 * (k - 1) + (j - 1))
    for i in range(1, 5):
        for j in range(1, 5):
            memory[32 + 4 * (i - 1) + (j - 1)] = sum(x(i, k) * y(k, j) for k in range(1, 5))


def memory_t13(memory, arrays):
    for a in range(10):  # W[i] = 9 - i
        memory[a] = 9 - a


MEMORY = {"T10": memory_t10, "T11": memory_t11, "T13": memory_t13}


def expected_memory(case_id, arrays):
    memory = oracle.initial_memory(arrays)
    if case_id in MEMORY:
        MEMORY[case_id](memory, arrays)
    return memory


def expected_scalars(spec, trace, arrays):
    """Scalars the program must end with: the fixed ones plus the sum of the initial values read from some arrays."""
    if spec is None:
        return {}
    by_name = {a.name: a for a in arrays}
    regions = [by_name[name].addresses() for name in spec["sum_of_reads"]["arrays"]]
    total = sum(oracle.init_value(a) for kind, a in trace if kind == "R" and any(a in r for r in regions))
    return {**spec["fixed"], spec["sum_of_reads"]["into"]: total}


# -- reading the optimizer's decisions ---------------------------------------------------------------

def decision_kind(entry):
    """Classify one entry of the L3 report: the kinds listed in the module docstring."""
    if entry.l2.report.status == "fallback":
        return "fallback"
    if entry.l2.plan is None:
        return "inside-fallback-window"
    if entry.levels.lam is None:
        return "constant"
    return "L3" if entry.applied else "kept-at-L2"


def load_expected(case_id):
    return json.loads((CASES_DIR / f"{case_id}.expected.json").read_text(encoding="utf-8"))


class MatrixTests(unittest.TestCase):
    def test_fixtures_exist_for_all_18_cases(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                self.assertTrue((CASES_DIR / f"{case_id}.ml").is_file())
                expected = load_expected(case_id)
                self.assertEqual((expected["id"], expected["program"]), (case_id, f"{case_id}.ml"))
                self.assertIn(case_id, ORACLE)
        self.assertEqual(sorted(p.name for p in CASES_DIR.glob("T*.ml")), [f"{c}.ml" for c in CASE_IDS])

    def run_case(self, case_id):
        expected = load_expected(case_id)
        source = (CASES_DIR / expected["program"]).read_text(encoding="utf-8")
        arrays, body = ORACLE[case_id]()
        trace = oracle.expected_trace(arrays, body)
        results = eq.assert_equivalent(self, source, trace, expected["program"])  # scalars, memory, traces
        return expected, arrays, trace, results

    def test_equivalence_of_every_case_at_every_level(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                _, _, _, results = self.run_case(case_id)
                self.assertEqual(list(results), list(eq.LEVELS))

    def test_oracle_trace_matches_the_hand_derived_summary(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                expected = load_expected(case_id)["trace"]
                trace = oracle.expected_trace(*ORACLE[case_id]())
                self.assertEqual(len(trace), expected["length"])
                if "first" in expected:
                    self.assertEqual(trace[0], tuple(expected["first"]))
                    self.assertEqual(trace[-1], tuple(expected["last"]))
                prefix = [tuple(x) for x in expected.get("prefix", [])]
                self.assertEqual(trace[:len(prefix)], prefix)

    def test_final_scalars_and_memory_at_every_level(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                expected, arrays, trace, results = self.run_case(case_id)
                scalars = expected_scalars(expected.get("scalars"), trace, arrays)
                memory = expected_memory(case_id, arrays)
                for level, result in results.items():
                    self.assertEqual(result.run.scalars, scalars, f"{case_id} {level} scalars")
                    self.assertEqual(result.run.memory, memory, f"{case_id} {level} memory")

    def test_matrix_product_t11(self):
        _, arrays, _, results = self.run_case("T11")
        z = [results["L3"].run.memory[32 + n] for n in range(16)]
        init = oracle.init_value
        want = [sum(init(4 * (i - 1) + (k - 1)) * init(16 + 4 * (k - 1) + (j - 1)) for k in range(1, 5))
                for i in range(1, 5) for j in range(1, 5)]
        self.assertEqual(z, want)
        self.assertEqual(z[0], sum(init(k - 1) * init(16 + 4 * (k - 1)) for k in range(1, 5)))  # Z[1,1]
        for level in eq.LEVELS:
            self.assertEqual([results[level].run.memory[32 + n] for n in range(16)], want, level)

    def test_optimization_decisions_k_d_and_hoisting(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                expected, _, _, results = self.run_case(case_id)
                entries = results["L3"].optimized.entries
                self.assertEqual([e.l2.report.source for e in entries], [a["source"] for a in expected["accesses"]])
                for entry, want in zip(entries, expected["accesses"]):
                    label = f"{case_id} {want['source']}"
                    self.assertEqual(entry.l2.report.is_write, want["kind"] == "write", label)
                    self.assertEqual(decision_kind(entry), want["decision"], f"{label}: {entry.decision}")
                    if "k" in want:
                        plan = entry.l2.plan
                        self.assertEqual((plan.k, dict(plan.ds)), (want["k"], want["d"]), label)
                        if entry.levels.lam is not None or want.get("hoisted_out_of"):
                            hoisted = [lv.loop.var for lv in entry.levels.invariant]
                            self.assertEqual(hoisted, want.get("hoisted_out_of", []), label)
                    if want["decision"] == "fallback":
                        self.assertIn(want["reason"], entry.l2.report.reason, label)
                        self.assertIn("NOT LINEAR", entry.decision)
                        self.assertTrue(entry.decision.startswith("kept at L1"), label)

    def test_every_stage_reports_the_same_accesses_and_applies_l2_to_linear_ones(self):
        for case_id in CASE_IDS:
            with self.subTest(case=case_id):
                expected, _, _, results = self.run_case(case_id)
                for stage in ("L2", "hoist", "L3"):
                    self.assertEqual(len(results[stage].optimized.entries), len(expected["accesses"]), stage)
                for entry, want in zip(results["L2"].optimized.entries, expected["accesses"]):
                    linear_access = want["decision"] not in ("fallback", "inside-fallback-window")
                    self.assertEqual(entry.applied, linear_access, f"{case_id} {want['source']}")

    def test_programs_without_a_linear_access_are_unchanged_from_l1(self):
        for case_id in ("T12", "T14"):  # every access is a fallback
            with self.subTest(case=case_id):
                _, _, _, results = self.run_case(case_id)
                for level in ("L2", "hoist", "L3"):
                    self.assertEqual(results[level].code.quads, results["L1"].code.quads, f"{case_id} {level}")

    def test_t13_keeps_the_fallback_window_and_still_optimizes_the_plain_access(self):
        _, _, _, results = self.run_case("T13")
        kinds = [decision_kind(e) for e in results["L3"].optimized.entries]
        self.assertEqual(kinds, ["kept-at-L2", "fallback", "inside-fallback-window"])

    def test_degenerate_shapes_do_not_crash_the_optimizer(self):  # T07, T15, T16 (roadmap 5.3)
        for case_id, accesses in (("T07", 1), ("T15", 0), ("T16", 5)):
            with self.subTest(case=case_id):
                _, _, trace, results = self.run_case(case_id)
                self.assertEqual(len(trace), accesses)
                for level in eq.LEVELS:
                    self.assertEqual(len(results[level].run.trace), accesses)

    def test_t15_all_levels_are_identical(self):
        _, _, _, results = self.run_case("T15")
        runs = [(r.run.scalars, r.run.memory, r.run.trace) for r in results.values()]
        self.assertTrue(all(run == runs[0] for run in runs))
        self.assertEqual(runs[0][2], [])


# -- 11.4 edge cases --------------------------------------------------------------------------------

class EdgeCaseTests(unittest.TestCase):
    def check(self, name, source, arrays, body, sum_arrays=("A",)):
        """Run all levels against the oracle; check final ``s`` (sum of initial values read) and return the results."""
        trace = oracle.expected_trace(arrays, body)
        results = eq.assert_equivalent(self, source, trace, name)
        if sum_arrays:
            spec = {"fixed": {}, "sum_of_reads": {"into": "s", "arrays": list(sum_arrays)}}
            for level, result in results.items():
                self.assertEqual(result.run.scalars, expected_scalars(spec, trace, arrays), f"{name} {level}")
        return trace, results

    def plans(self, results):
        return [(e.l2.plan.k, dict(e.l2.plan.ds)) if e.l2.plan else None for e in results["L3"].optimized.entries]

    # zero-trip and single-iteration loops

    def test_zero_trip_inner_loop_with_a_statement_after_it(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 3 do\n  for j = 2 to 1 do s = s + A[i, j]; end\n"
                  "  s = s + A[i, 1];\nend\n")
        body = loops(("i", 1, 3))([Loop("j", 2, 1, reads_stmt(read("A", "i", "j"))), *reads_stmt(read("A", "i", 1))])
        trace, _ = self.check("zero-trip inner", source, [A], body)
        self.assertEqual(trace, [("R", 1000), ("R", 1020), ("R", 1040)])

    def test_zero_trip_outer_loop(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 3 to 2 do for j = 1 to 5 do s = s + A[i, j]; end end\n"
        trace, results = self.check("zero-trip outer", source, [A], loops(("i", 3, 2), ("j", 1, 5))(reads_stmt(read("A", "i", "j"))))
        self.assertEqual(trace, [])
        self.assertEqual({r.run.scalars["s"] for r in results.values()}, {0})

    def test_zero_trip_negative_range(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = -1 to -3 do s = s + A[1, 1]; end\n"
        trace, _ = self.check("zero-trip negative", source, [A], loops(("i", -1, -3))(reads_stmt(read("A", 1, 1))))
        self.assertEqual(trace, [])

    def test_single_iteration_loops(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 7 to 7 do for j = 4 to 4 do s = s + A[i, j]; end end\n")
        trace, results = self.check("single iteration", source, [A], loops(("i", 7, 7), ("j", 4, 4))(reads_stmt(read("A", "i", "j"))))
        self.assertEqual(trace, [("R", 1000 + 6 * 20 + 3)])  # Eq. 1: 1123
        self.assertEqual(self.plans(results), [(979, {"i": 20, "j": 1})])

    def test_single_iteration_inner_loop_inside_a_longer_outer_loop(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 4 do for j = 3 to 3 do s = s + A[i, j]; end end\n"
        trace, _ = self.check("single inner", source, [A], loops(("i", 1, 4), ("j", 3, 3))(reads_stmt(read("A", "i", "j"))))
        self.assertEqual(trace, [("R", 1002), ("R", 1022), ("R", 1042), ("R", 1062)])

    # a dimension of size 1

    def test_size_one_middle_dimension(self):
        g = Array("G", (1, 1, 1), (3, 1, 4), 20)  # sizes (3, 1, 4), strides (4, 4, 1)
        self.assertEqual(g.strides, (4, 4, 1))
        source = "array G[1:3, 1:1, 1:4] at 20;\ns = 0;\nfor i = 1 to 3 do for k = 1 to 4 do s = s + G[i, 1, k]; end end\n"
        trace, results = self.check("size-1 middle", source, [g], loops(("i", 1, 3), ("k", 1, 4))(reads_stmt(read("G", "i", 1, "k"))),
                                    ("G",))
        self.assertEqual(trace, [("R", a) for a in range(20, 32)])
        # a - C = 20 - ((1*1 + 1)*4 + 1) = 11, plus D0 = 1*4 for the constant middle subscript: K = 15
        self.assertEqual(self.plans(results), [(15, {"i": 4, "k": 1})])

    def test_one_element_one_dimensional_array(self):
        v = Array("V1", (7,), (7,), 3)
        source = "array V1[7:7] at 3;\ns = 0;\nfor i = 7 to 7 do s = s + V1[i]; end\n"
        trace, _ = self.check("1-element array", source, [v], loops(("i", 7, 7))(reads_stmt(read("V1", "i"))), ("V1",))
        self.assertEqual(trace, [("R", 3)])

    def test_all_dimensions_of_size_one(self):
        u = Array("U1", (1, 1), (1, 1), 0)
        source = "array U1[1:1, 1:1] at 0;\ns = 0;\nfor i = 1 to 3 do s = s + U1[1, 1]; end\n"
        trace, _ = self.check("1x1 array", source, [u], loops(("i", 1, 3))(reads_stmt(read("U1", 1, 1))), ("U1",))
        self.assertEqual(trace, [("R", 0)] * 3)

    # negative bounds

    def test_array_and_loops_entirely_negative(self):
        r = Array("R", (-10, -8), (-5, -2), 40)  # sizes (6, 7), strides (7, 1), 42 cells
        source = ("array R[-10:-5, -8:-2] at 40;\ns = 0;\nfor i = -10 to -5 do for j = -8 to -2 do s = s + R[i, j]; end end\n")
        trace, results = self.check("negative bounds", source, [r], loops(("i", -10, -5), ("j", -8, -2))(reads_stmt(read("R", "i", "j"))),
                                    ("R",))
        self.assertEqual(trace, [("R", a) for a in range(40, 82)])
        self.assertEqual(self.plans(results), [(118, {"i": 7, "j": 1})])  # K = a - C = 40 + 78

    def test_negative_bound_with_a_descending_subscript(self):
        n = Array("Q", (-4,), (4,), 0)
        source = "array Q[-4:4] at 0;\ns = 0;\nfor i = -4 to 4 do s = s + Q[-i]; end\n"
        trace, results = self.check("descending", source, [n], loops(("i", -4, 4))(reads_stmt(read("Q", linear(0, i=-1)))), ("Q",))
        self.assertEqual(trace, [("R", a) for a in range(8, -1, -1)])
        self.assertEqual(self.plans(results), [(4, {"i": -1})])

    # negative and zero coefficients

    def test_negative_and_zero_coefficients(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 5 do for j = 1 to 3 do s = s + A[12 - 2*i, 3 + 0*j]; end end\n")
        body = loops(("i", 1, 5), ("j", 1, 3))(reads_stmt(read("A", linear(12, i=-2), linear(3, j=0))))
        trace, results = self.check("neg and zero coefficient", source, [A], body)
        # address = 1000 + (12 - 2i - 1)*20 + 2 = 1222 - 40 i; independent of j
        self.assertEqual(trace[:3], [("R", 1182)] * 3)
        self.assertEqual(trace[-1], ("R", 1222 - 200))
        self.assertEqual(self.plans(results), [(1222, {"i": -40, "j": 0})])

    def test_negative_coefficient_on_the_inner_loop(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 2 do for j = 1 to 20 do s = s + A[i, 21 - j]; end end\n"
        body = loops(("i", 1, 2), ("j", 1, 20))(reads_stmt(read("A", "i", linear(21, j=-1))))
        trace, results = self.check("neg inner coefficient", source, [A], body)
        self.assertEqual(trace[:3], [("R", 1019), ("R", 1018), ("R", 1017)])
        self.assertEqual(self.plans(results), [(979 + 21, {"i": 20, "j": -1})])

    def test_coefficient_larger_than_one_and_zero_on_a_loop(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 3 do for j = 1 to 4 do s = s + A[3*i, 2*j]; end end\n"
        body = loops(("i", 1, 3), ("j", 1, 4))(reads_stmt(read("A", linear(0, i=3), linear(0, j=2))))
        trace, results = self.check("coefficients 3 and 2", source, [A], body)
        self.assertEqual(trace[0], ("R", 1000 + 2 * 20 + 1))
        self.assertEqual(self.plans(results), [(979, {"i": 60, "j": 2})])

    # an access independent of a loop

    def test_access_independent_of_the_inner_loop_is_hoisted(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 4 do for j = 1 to 3 do s = s + A[i, 5]; end end\n"
        trace, results = self.check("independent of j", source, [A], loops(("i", 1, 4), ("j", 1, 3))(reads_stmt(read("A", "i", 5))))
        self.assertEqual(trace[:4], [("R", 1004)] * 3 + [("R", 1024)])
        entry = results["L3"].optimized.entries[0]
        self.assertEqual([lv.loop.var for lv in entry.levels.invariant], ["j"])

    def test_access_independent_of_the_middle_and_the_outer_loop(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 3 do for j = 1 to 2 do for k = 1 to 4 do\n"
                  "  s = s + A[i, k] + A[j, k];\nend end end\n")
        body = loops(("i", 1, 3), ("j", 1, 2), ("k", 1, 4))(reads_stmt(read("A", "i", "k"), read("A", "j", "k")))
        trace, results = self.check("independent of loops", source, [A], body)
        self.assertEqual(self.plans(results), [(979, {"i": 20, "j": 0, "k": 1}), (979, {"i": 0, "j": 20, "k": 1})])
        self.assertEqual(len(trace), 48)

    # the same element twice in one statement

    def test_same_element_twice_in_one_statement(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 2 do for j = 1 to 3 do s = s + A[i, j] + A[i, j]; end end\n")
        body = loops(("i", 1, 2), ("j", 1, 3))(reads_stmt(read("A", "i", "j"), read("A", "i", "j")))
        trace, results = self.check("same element twice", source, [A], body)
        self.assertEqual(trace[:4], [("R", 1000), ("R", 1000), ("R", 1001), ("R", 1001)])

    def test_read_and_write_of_the_same_element(self):
        v = Array("V", (1,), (6,), 10)
        source = "array V[1:6] at 10;\nfor i = 1 to 6 do V[i] = V[i] * V[i]; end\n"
        body = loops(("i", 1, 6))([assign(write("V", "i"), read("V", "i"), read("V", "i"))])
        trace, results = self.check("square in place", source, [v], body, None)
        self.assertEqual(trace[:3], [("R", 10), ("R", 10), ("W", 10)])
        memory = oracle.initial_memory([v])
        for a in range(10, 16):
            memory[a] = oracle.init_value(a) ** 2
        for level, result in results.items():
            self.assertEqual(result.run.memory, memory, level)

    def test_update_in_place_with_a_constant(self):
        source = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 3 do for j = 1 to 4 do A[i, j] = A[i, j] + 1; end end\n"
        body = loops(("i", 1, 3), ("j", 1, 4))([assign(write("A", "i", "j"), read("A", "i", "j"))])
        trace, results = self.check("increment in place", source, [A], body, None)
        memory = oracle.initial_memory([A])
        for i in range(3):
            for j in range(4):
                memory[1000 + 20 * i + j] += 1
        for level, result in results.items():
            self.assertEqual(result.run.memory, memory, level)

    # a 4-D array

    def test_four_dimensions_with_permuted_subscripts(self):
        q = Array("Q", (0, 0, 0, 0), (2, 2, 2, 2), 7)  # strides (27, 9, 3, 1)
        self.assertEqual(q.strides, (27, 9, 3, 1))
        source = ("array Q[0:2, 0:2, 0:2, 0:2] at 7;\ns = 0;\nfor i = 0 to 2 do for j = 0 to 2 do for k = 0 to 2 do for m = 0 to 2 do\n"
                  "  s = s + Q[j, i, m, k];\nend end end end\n")
        body = loops(("i", 0, 2), ("j", 0, 2), ("k", 0, 2), ("m", 0, 2))(reads_stmt(read("Q", "j", "i", "m", "k")))
        trace, results = self.check("4-D permuted", source, [q], body, ("Q",))
        self.assertEqual(len(trace), 81)
        self.assertEqual(trace[1], ("R", 7 + 3))  # m = 1 is the third subscript: stride 3
        self.assertEqual(self.plans(results), [(7, {"i": 9, "j": 27, "k": 1, "m": 3})])

    def test_four_dimensions_with_negative_bounds_and_linear_subscripts(self):
        p = Array("P", (-1, 0, 2, -2), (1, 2, 4, 0), 500)  # sizes (3, 3, 3, 3), strides (27, 9, 3, 1)
        source = ("array P[-1:1, 0:2, 2:4, -2:0] at 500;\ns = 0;\nfor i = -1 to 1 do for j = 0 to 2 do for k = 2 to 4 do for m = -2 to 0 do\n"
                  "  s = s + P[i, j, k, m];\nend end end end\n")
        body = loops(("i", -1, 1), ("j", 0, 2), ("k", 2, 4), ("m", -2, 0))(reads_stmt(read("P", "i", "j", "k", "m")))
        trace, results = self.check("4-D negative", source, [p], body, ("P",))
        self.assertEqual(trace, [("R", a) for a in range(500, 581)])
        self.assertEqual(self.plans(results), [(523, {"i": 27, "j": 9, "k": 3, "m": 1})])  # K = 500 + 27 - 6 + 2

    def test_four_dimensions_with_reversed_linear_subscripts(self):
        q = Array("Q", (0, 0, 0, 0), (2, 2, 2, 2), 7)
        source = ("array Q[0:2, 0:2, 0:2, 0:2] at 7;\ns = 0;\nfor i = 0 to 2 do for j = 0 to 2 do for k = 0 to 2 do for m = 0 to 2 do\n"
                  "  s = s + Q[2 - i, j, k, 2 - m];\nend end end end\n")
        body = loops(("i", 0, 2), ("j", 0, 2), ("k", 0, 2), ("m", 0, 2))(
            reads_stmt(read("Q", linear(2, i=-1), "j", "k", linear(2, m=-1))))
        trace, results = self.check("4-D reversed", source, [q], body, ("Q",))
        self.assertEqual(trace[:3], [("R", 7 + 54 + 2), ("R", 7 + 54 + 1), ("R", 7 + 54)])
        self.assertEqual(self.plans(results), [(7 + 54 + 2, {"i": -27, "j": 9, "k": 3, "m": -1})])


if __name__ == "__main__":
    unittest.main()
