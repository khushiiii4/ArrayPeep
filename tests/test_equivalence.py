"""Tests for tests/equivalence.py (Phase 11.2: the three-way equivalence harness).

Oracle traces are described by hand (docs/hand_examples.md) or built with
tests/oracle.py; nothing here is taken from the compiler's own output.
"""
import dataclasses
import pathlib
import sys
import unittest
from unittest import mock

TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:  # also works for `python -m unittest tests.test_equivalence`
    sys.path.insert(0, str(TESTS_DIR))

import equivalence as eq  # noqa: E402
import oracle  # noqa: E402
from oracle import Array, Loop, Statement, assign, linear, read, write  # noqa: E402

A = Array("A", (1, 1), (10, 20), 1000)
V = Array("V", (5,), (14,), 500)

ONE_D_SUM = [
    Loop("i", 5, 14, [assign(write("V", "i"))]),
    Loop("i", 5, 14, [Statement([read("V", "i")])]),
]
ONE_D_SUM_TRACE = [("W", a) for a in range(500, 510)] + [("R", a) for a in range(500, 510)]  # hand_examples.md 5
TRAVERSAL = [Loop("i", 1, 10, [Loop("j", 1, 20, [Statement([read("A", "i", "j")])])])]
TRAVERSAL_TRACE = [("R", a) for a in range(1000, 1200)]  # hand_examples.md 6
T04 = [Loop("i", 1, 4, [Loop("j", 2, 20, [Statement([read("A", linear(1, i=2), linear(-1, j=1))])])])]
OUT_OF_BOUNDS = "array A[1:2] at 0;\nx = A[5];\n"


class LevelRunTests(unittest.TestCase):
    def test_all_five_levels_are_run_in_order(self):
        results, findings = eq.run_levels(eq.read_example("1d_sum.ml"), "1d_sum.ml")
        self.assertEqual(findings, [])
        self.assertEqual(eq.LEVELS, ("L0", "L1", "L2", "hoist", "L3"))
        self.assertEqual(list(results), list(eq.LEVELS))
        got = {level: (r.baseline_level, r.stage) for level, r in results.items()}
        self.assertEqual(got, {"L0": ("L0", None), "L1": ("L1", None), "L2": ("L1", "L2"),
                               "hoist": ("L1", "hoist"), "L3": ("L1", "L3")})

    def test_a_runtime_error_is_a_finding_for_that_level_only(self):
        real = eq.run_source

        def flaky(source, baseline, stage, path=None):
            if stage == "hoist":
                return real(OUT_OF_BOUNDS, baseline, stage)
            return real(source, baseline, stage, path=path)

        with mock.patch.object(eq, "run_source", flaky):
            results, findings = eq.run_levels("s = 0;\n", "prog")
        self.assertEqual([f.level for f in findings], ["hoist"])
        self.assertEqual((findings[0].program, findings[0].comparison), ("prog", "run"))
        self.assertIn("RuntimeAccessError", findings[0].difference)
        self.assertEqual(list(results), ["L0", "L1", "L2", "L3"])

    def test_runtime_error_at_every_level_is_reported_for_every_level(self):
        _, findings = eq.check_program(OUT_OF_BOUNDS, [], "oob")
        self.assertEqual([(f.level, f.comparison) for f in findings], [(lvl, "run") for lvl in eq.LEVELS])


class PassingProgramTests(unittest.TestCase):
    def test_oracle_builder_reproduces_the_hand_traces(self):
        self.assertEqual(oracle.expected_trace([V], ONE_D_SUM), ONE_D_SUM_TRACE)
        self.assertEqual(oracle.expected_trace([A], TRAVERSAL), TRAVERSAL_TRACE)

    def test_1d_sum(self):
        results = eq.assert_equivalent(self, eq.read_example("1d_sum.ml"), ONE_D_SUM_TRACE, "1d_sum.ml")
        for level, result in results.items():
            self.assertEqual(result.run.scalars["s"], 190, level)  # hand_examples.md 5

    def test_2d_traversal(self):
        eq.assert_equivalent(self, eq.read_example("2d_traversal.ml"), TRAVERSAL_TRACE, "2d_traversal.ml")

    def test_t04_linear_subscripts(self):
        trace = oracle.expected_trace([A], T04)
        self.assertEqual((len(trace), trace[0], trace[-1]), (76, ("R", 1040), ("R", 1178)))
        results = eq.assert_equivalent(self, eq.read_example("linear_subscripts.ml"), trace, "linear_subscripts.ml")
        self.assertEqual({r.run.trace[0] for r in results.values()}, {("R", 1040)})

    def test_zero_trip_program_has_an_empty_trace(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 5 to 1 do s = s + A[i, 1]; end\n"
        eq.assert_equivalent(self, source, [], "zero-trip")


class FailureReportTests(unittest.TestCase):
    """The harness must detect, and name, each kind of disagreement."""

    @classmethod
    def setUpClass(cls):
        results, findings = eq.check_program(eq.read_example("1d_sum.ml"), ONE_D_SUM_TRACE, "1d_sum.ml")
        assert not findings
        cls.runs = {level: r.run for level, r in results.items()}

    def broken(self, level, **changes):
        runs = dict(self.runs)
        runs[level] = dataclasses.replace(runs[level], **changes)
        return runs

    def test_wrong_oracle_trace_is_reported_for_every_level(self):
        wrong = ONE_D_SUM_TRACE[:5] + [("R", 999)] + ONE_D_SUM_TRACE[6:]
        findings = eq.find_differences("prog", self.runs, wrong)
        self.assertEqual([(f.level, f.comparison) for f in findings], [(lvl, eq.TRACE_ORACLE) for lvl in eq.LEVELS])
        self.assertIn("differs at access 6: oracle (R, 999) vs L0 (W, 505)", findings[0].difference)

    def test_trace_length_difference(self):
        findings = eq.find_differences("prog", self.runs, ONE_D_SUM_TRACE + [("R", 510)])
        self.assertIn("access 21: oracle (R, 510) vs L0 (end of trace)", findings[0].difference)
        findings = eq.find_differences("prog", self.runs, ONE_D_SUM_TRACE[:-1])
        self.assertIn("access 20: oracle (end of trace) vs L0 (R, 509)", findings[0].difference)

    def test_scalar_difference_in_one_level(self):
        runs = self.broken("L2", scalars={**self.runs["L2"].scalars, "s": 191})
        findings = eq.find_differences("prog", runs, ONE_D_SUM_TRACE)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual((f.program, f.level, f.comparison), ("prog", "L2", eq.SCALARS))
        self.assertIn("scalar s differs: L0 190 vs L2 191", f.difference)

    def test_memory_difference_in_one_level(self):
        memory = dict(self.runs["hoist"].memory)
        memory[503] += 1
        findings = eq.find_differences("prog", self.broken("hoist", memory=memory), ONE_D_SUM_TRACE)
        self.assertEqual([(f.level, f.comparison) for f in findings], [("hoist", eq.MEMORY)])
        self.assertIn("memory differs at address 503", findings[0].difference)

    def test_trace_difference_against_l0_and_the_oracle(self):
        trace = list(self.runs["L3"].trace)
        trace[12] = ("R", 777)
        findings = eq.find_differences("prog", self.broken("L3", trace=trace), ONE_D_SUM_TRACE)
        self.assertEqual([(f.level, f.comparison) for f in findings], [("L3", eq.TRACE_L0), ("L3", eq.TRACE_ORACLE)])
        self.assertIn("differs at access 13: L0 (R, 502) vs L3 (R, 777)", findings[0].difference)
        self.assertIn("differs at access 13: oracle (R, 502) vs L3 (R, 777)", findings[1].difference)

    def test_a_wrong_baseline_is_caught_by_the_oracle_even_when_all_levels_agree(self):
        # all levels equal L0, but L0 itself disagrees with the oracle: only the oracle can see it
        shifted = [(kind, address + 1) for kind, address in ONE_D_SUM_TRACE]
        findings = eq.find_differences("prog", self.runs, shifted)
        self.assertEqual({f.comparison for f in findings}, {eq.TRACE_ORACLE})
        self.assertEqual(len(findings), 5)

    def test_all_three_checks_are_evaluated_for_one_level(self):
        runs = self.broken("L1", scalars={}, memory={}, trace=[])
        comparisons = [f.comparison for f in eq.find_differences("prog", runs, ONE_D_SUM_TRACE) if f.level == "L1"]
        self.assertEqual(comparisons, [eq.SCALARS, eq.MEMORY, eq.TRACE_L0, eq.TRACE_ORACLE])

    def test_missing_level_is_skipped_and_missing_baseline_leaves_only_oracle_checks(self):
        runs = {level: run for level, run in self.runs.items() if level != "L1"}
        self.assertEqual(eq.find_differences("prog", runs, ONE_D_SUM_TRACE), [])
        no_l0 = {level: run for level, run in self.runs.items() if level != "L0"}
        self.assertEqual(eq.find_differences("prog", no_l0, ONE_D_SUM_TRACE), [])
        self.assertEqual({f.comparison for f in eq.find_differences("prog", no_l0, [])}, {eq.TRACE_ORACLE})

    def test_finding_text_names_program_level_comparison_and_difference(self):
        finding = eq.Finding("p.ml", "L3", eq.SCALARS, "scalar s differs: L0 1 vs L3 2")
        self.assertEqual(str(finding), "p.ml: L3: scalars: scalar s differs: L0 1 vs L3 2")


class AssertionTests(unittest.TestCase):
    def test_assert_equivalent_fails_with_one_line_per_finding(self):
        wrong = [("R", a) for a in range(1001, 1201)]
        with self.assertRaises(AssertionError) as caught:
            eq.assert_equivalent(self, eq.read_example("2d_traversal.ml"), wrong, "2d_traversal.ml")
        message = str(caught.exception)
        self.assertIn("5 equivalence failure(s)", message)
        for level in eq.LEVELS:
            self.assertIn(f"2d_traversal.ml: {level}: address trace vs oracle: differs at access 1: "
                          f"oracle (R, 1001) vs {level} (R, 1000)", message)

    def test_check_program_returns_findings_without_raising(self):
        _, findings = eq.check_program(eq.read_example("1d_sum.ml"), [], "empty-oracle")
        self.assertEqual({f.level for f in findings}, set(eq.LEVELS))
        self.assertTrue(all(f.program == "empty-oracle" for f in findings))


if __name__ == "__main__":
    unittest.main()
