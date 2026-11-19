"""Tests for the Phase 10.3 equivalence verification (arraypeep.driver and the --compare option).

Run results for the pure comparison tests are built by hand, so every
expected difference text is derived from the inputs. A
verification FAIL exits with code 3 (Phase 10.5); run-time errors in compare mode are covered in test_exit_codes.
"""
import contextlib
import dataclasses
import io
import pathlib
import tempfile
import unittest
from unittest import mock

from arraypeep import __main__ as cli
from arraypeep import driver
from arraypeep.driver import (
    CHECKS,
    CheckResult,
    DriverError,
    LevelComparison,
    LevelOutcome,
    Verification,
    compare_runs,
    run_file,
    verify,
)
from arraypeep.interpreter import (
    InternalError,
    RunResult,
    RuntimeAccessError,
    StepLimitError,
)
from arraypeep.loop_opt import StageResult, optimize_stage
from arraypeep.metrics import Counters
from arraypeep.report import format_header, format_verification
from arraypeep.symbol_table import ArrayPeepError

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))
RUNS = cli.LEVEL_RUNS
LEVELS = tuple(RUNS)

ARRAY = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 9 do for j = 1 to 19 do s = s + A[i + 1, j]; end end\n"
MIXED = (
    "array A[1:10, 1:20] at 1000; array V[0:9] at 100; array W[0:99] at 2000;\n"
    "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j] + V[i] + A[3, 4] + W[i * j]; end end\n"
)
ZERO_TRIP = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 5 to 1 do s = s + A[i, j] + A[i, 3]; end end\n"
FALLBACK = (
    "array A[1:4, 1:5] at 0; array V[0:1100] at 5000;\n"
    "for i = 1 to 4 do for j = 1 to 5 do s = s + V[A[i, j] + 1] + A[i, j]; end end\n"
)


def make_run(scalars=None, memory=None, trace=None, level=None) -> RunResult:
    return RunResult(
        {"s": 5} if scalars is None else scalars,
        {10: 1, 11: 2, 12: 3} if memory is None else memory,
        [("R", 10), ("R", 11), ("W", 12)] if trace is None else trace,
        Counters(),
        level,
    )


def outcome(level: str, **kwargs) -> LevelOutcome:
    return LevelOutcome(level, mock.Mock(run=make_run(**kwargs)))


class CompareRunsTests(unittest.TestCase):
    def test_equal_runs_pass(self):
        c = compare_runs("L0", make_run(), "L2", make_run())
        self.assertIsInstance(c, LevelComparison)
        self.assertTrue(c.passed)
        self.assertIsNone(c.first_difference)
        self.assertIsNone(c.error)
        self.assertEqual((c.baseline, c.level), ("L0", "L2"))
        self.assertEqual([(k.name, k.passed, k.difference) for k in c.checks],
                         [("scalars", True, None), ("memory", True, None), ("address trace", True, None)])
        self.assertEqual(tuple(k.name for k in c.checks), CHECKS)

    def test_counters_are_not_compared(self):
        other = make_run()
        other.counters.record("+", "ADDR")
        self.assertTrue(compare_runs("L0", make_run(), "L3", other).passed)

    def test_the_level_label_of_a_run_is_not_compared(self):
        self.assertTrue(compare_runs("L0", make_run(level="L0"), "L3", make_run(level="L3")).passed)

    def test_scalar_mismatch_only(self):
        c = compare_runs("L0", make_run(), "L2", make_run(scalars={"s": 6}))
        self.assertFalse(c.passed)
        self.assertEqual([k.passed for k in c.checks], [False, True, True])
        self.assertEqual(c.first_difference, "scalar s differs: L0 5 vs L2 6")

    def test_memory_mismatch_only(self):
        c = compare_runs("L0", make_run(), "L2", make_run(memory={10: 1, 11: 9, 12: 3}))
        self.assertEqual([k.passed for k in c.checks], [True, False, True])
        self.assertEqual(c.first_difference, "memory differs at address 11: L0 2 vs L2 9")

    def test_trace_mismatch_only(self):
        c = compare_runs("L0", make_run(), "L2", make_run(trace=[("R", 10), ("R", 12), ("W", 12)]))
        self.assertEqual([k.passed for k in c.checks], [True, True, False])
        self.assertEqual(c.first_difference, "address trace differs at access 2: L0 (R, 11) vs L2 (R, 12)")

    def test_trace_kind_difference_is_detected(self):
        c = compare_runs("L0", make_run(), "L2", make_run(trace=[("R", 10), ("R", 11), ("R", 12)]))
        self.assertFalse(c.checks[2].passed)
        self.assertEqual(c.checks[2].difference, "address trace differs at access 3: L0 (W, 12) vs L2 (R, 12)")

    def test_all_three_checks_are_always_evaluated(self):
        c = compare_runs("L0", make_run(), "L2",
                         make_run(scalars={"s": 6}, memory={10: 0, 11: 2, 12: 3}, trace=[("R", 10)]))
        self.assertEqual([k.passed for k in c.checks], [False, False, False])
        self.assertTrue(all(k.difference for k in c.checks))

    def test_first_difference_is_the_first_failing_check_in_order(self):
        c = compare_runs("L0", make_run(), "L2", make_run(memory={10: 0, 11: 2, 12: 3}, trace=[("R", 10)]))
        self.assertEqual(c.first_difference, "memory differs at address 10: L0 1 vs L2 0")
        c = compare_runs("L0", make_run(), "L2", make_run(scalars={"s": 6}, trace=[]))
        self.assertTrue(c.first_difference.startswith("scalar s differs"))

    # -- first-difference rules -------------------------------------------------

    def test_scalars_first_difference_is_the_smallest_name(self):
        base = make_run(scalars={"a": 1, "b": 2, "c": 3})
        other = make_run(scalars={"a": 1, "b": 9, "c": 8})
        self.assertEqual(compare_runs("L0", base, "L1", other).first_difference, "scalar b differs: L0 2 vs L1 9")
        other = make_run(scalars={"a": 0, "b": 9, "c": 3})
        self.assertEqual(compare_runs("L0", base, "L1", other).first_difference, "scalar a differs: L0 1 vs L1 0")

    def test_scalar_missing_in_the_level(self):
        c = compare_runs("L0", make_run(scalars={"s": 1, "t": 2}), "L2", make_run(scalars={"s": 1}))
        self.assertEqual(c.first_difference, "scalar t is missing in L2: L0 2")

    def test_scalar_missing_in_the_baseline(self):
        c = compare_runs("L0", make_run(scalars={"s": 1}), "L2", make_run(scalars={"s": 1, "t": 2}))
        self.assertEqual(c.first_difference, "scalar t is missing in L0: L2 2")

    def test_missing_scalar_sorts_with_the_others(self):
        base = make_run(scalars={"a": 1, "z": 1})
        other = make_run(scalars={"b": 1, "z": 2})
        self.assertEqual(compare_runs("L0", base, "L2", other).first_difference, "scalar a is missing in L2: L0 1")

    def test_memory_first_difference_is_the_lowest_address(self):
        base = make_run(memory={1: 1, 2: 2, 3: 3})
        other = make_run(memory={1: 1, 2: 0, 3: 0})
        self.assertEqual(compare_runs("L0", base, "L2", other).first_difference, "memory differs at address 2: L0 2 vs L2 0")

    def test_memory_address_missing_in_either_image(self):
        base = make_run(memory={1: 1, 2: 2})
        self.assertEqual(compare_runs("L0", base, "L2", make_run(memory={1: 1})).first_difference,
                         "memory address 2 is missing in L2: L0 2")
        self.assertEqual(compare_runs("L0", make_run(memory={1: 1}), "L2", base).first_difference,
                         "memory address 2 is missing in L0: L2 2")

    def test_negative_addresses_sort_numerically(self):
        base = make_run(memory={-5: 1, 3: 1, 10: 1})
        other = make_run(memory={-5: 2, 3: 2, 10: 2})
        self.assertEqual(compare_runs("L0", base, "L2", other).first_difference, "memory differs at address -5: L0 1 vs L2 2")

    def test_trace_ending_early_in_the_level(self):
        c = compare_runs("L0", make_run(), "L2", make_run(trace=[("R", 10), ("R", 11)]))
        self.assertEqual(c.first_difference, "address trace differs at access 3: L0 (W, 12) vs L2 (end of trace)")

    def test_trace_ending_early_in_the_baseline(self):
        c = compare_runs("L0", make_run(trace=[("R", 10)]), "L2", make_run())
        self.assertEqual(c.first_difference, "address trace differs at access 2: L0 (end of trace) vs L2 (R, 11)")

    def test_empty_traces_are_equal(self):
        self.assertTrue(compare_runs("L0", make_run(trace=[]), "L2", make_run(trace=[])).passed)

    def test_trace_index_is_one_based(self):
        c = compare_runs("L0", make_run(trace=[("R", 1)]), "L2", make_run(trace=[("R", 2)]))
        self.assertIn("at access 1:", c.first_difference)

    def test_comparison_is_deterministic(self):
        pair = (make_run(scalars={"x": 1, "a": 2}), make_run(scalars={"x": 2, "a": 3}))
        results = [compare_runs("L0", pair[0], "L2", pair[1]) for _ in range(5)]
        self.assertTrue(all(r == results[0] for r in results))

    def test_result_types_are_frozen(self):
        c = compare_runs("L0", make_run(), "L2", make_run())
        for obj, field in ((c, "level"), (c.checks[0], "name"), (Verification("L0", ()), "baseline")):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(obj, field, "x")

    def test_inputs_are_not_modified(self):
        a, b = make_run(), make_run(scalars={"s": 6})
        before = (dict(a.scalars), dict(a.memory), list(a.trace), dict(b.scalars))
        compare_runs("L0", a, "L2", b)
        self.assertEqual((a.scalars, a.memory, a.trace, b.scalars), before)


class VerifyTests(unittest.TestCase):
    def test_all_levels_equal(self):
        v = verify([outcome(n) for n in LEVELS])
        self.assertIsInstance(v, Verification)
        self.assertEqual(v.baseline, "L0")
        self.assertEqual([c.level for c in v.comparisons], ["L1", "L2", "hoist", "L3"])
        self.assertTrue(v.passed)
        self.assertEqual(v.verdict, "PASS")

    def test_every_non_baseline_level_is_compared_with_l0(self):
        v = verify([outcome(n) for n in LEVELS])
        self.assertEqual({c.baseline for c in v.comparisons}, {"L0"})

    def test_one_failing_level_fails_the_verdict(self):
        outs = [outcome(n) for n in LEVELS]
        outs[3] = outcome("hoist", scalars={"s": 0})
        v = verify(outs)
        self.assertFalse(v.passed)
        self.assertEqual(v.verdict, "FAIL")
        self.assertEqual([c.passed for c in v.comparisons], [True, True, False, True])

    def test_each_category_fails_the_verdict(self):
        for kwargs in ({"scalars": {"s": 0}}, {"memory": {}}, {"trace": []}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(verify([outcome("L0"), outcome("L2", **kwargs)]).verdict, "FAIL")

    def test_custom_baseline(self):
        v = verify([outcome("L0"), outcome("L1"), outcome("L2")], baseline="L1")
        self.assertEqual([(c.baseline, c.level) for c in v.comparisons], [("L1", "L0"), ("L1", "L2")])

    def test_baseline_only_gives_an_empty_passing_verification(self):
        v = verify([outcome("L0")])
        self.assertEqual((v.comparisons, v.verdict), ((), "PASS"))

    def test_missing_baseline(self):
        with self.assertRaises(DriverError) as ctx:
            verify([outcome("L1"), outcome("L2")])
        self.assertIn("baseline level 'L0'", str(ctx.exception))

    def test_outcome_without_result_or_error_is_rejected(self):
        with self.assertRaises(DriverError):
            verify([outcome("L0"), LevelOutcome("L2")])
        with self.assertRaises(DriverError):
            verify([LevelOutcome("L0"), outcome("L2")])

    def test_runtime_error_in_a_level_is_a_failed_comparison(self):
        error = RuntimeAccessError("runtime error: bad address", 4, "L2")
        v = verify([outcome("L0"), LevelOutcome("L2", None, error), outcome("L3")])
        c = v.comparisons[0]
        self.assertFalse(c.passed)
        self.assertIs(c.error, error)
        self.assertEqual(c.checks, ())
        self.assertEqual(c.first_difference, "runtime error at quadruple 4 where L0 completed")
        self.assertEqual(v.verdict, "FAIL")
        self.assertTrue(v.comparisons[1].passed)  # the other levels are still compared

    def test_step_limit_error_in_a_level_is_a_failed_comparison(self):
        error = StepLimitError("runtime error: step limit exceeded", None, "L3")
        c = verify([outcome("L0"), LevelOutcome("L3", None, error)]).comparisons[0]
        self.assertEqual(c.first_difference, "step limit exceeded where L0 completed")
        self.assertFalse(c.passed)

    def test_baseline_execution_error_propagates(self):
        for error in (RuntimeAccessError("runtime error: x", 2, "L0"), StepLimitError("runtime error: y")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as ctx:
                    verify([LevelOutcome("L0", None, error), outcome("L2")])
                self.assertIs(ctx.exception, error)

    def test_internal_error_propagates(self):
        error = InternalError("internal error: T3 read before assignment", 3, "L2")
        with self.assertRaises(InternalError) as ctx:
            verify([outcome("L0"), LevelOutcome("L2", None, error)])
        self.assertIs(ctx.exception, error)

    def test_other_execution_errors_are_not_swallowed(self):
        from arraypeep.interpreter import ExecutionError

        with self.assertRaises(ExecutionError):
            verify([outcome("L0"), LevelOutcome("L2", None, ExecutionError("odd"))])

    def test_verify_is_deterministic(self):
        outs = [outcome("L0"), outcome("L2", scalars={"s": 1}), outcome("L3", trace=[])]
        self.assertEqual(verify(outs), verify(outs))


class PipelineVerificationTests(unittest.TestCase):
    """Real programs through the real pipeline: every level must verify as PASS."""

    @staticmethod
    def outcomes(path):
        return [LevelOutcome(n, run_file(path, *RUNS[n])) for n in LEVELS]

    def check_program(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "p.ml"
            path.write_text(text, encoding="utf-8")
            return verify(self.outcomes(path))

    def test_all_four_comparisons_pass_on_a_loop_program(self):
        v = self.check_program(ARRAY)
        self.assertEqual([c.level for c in v.comparisons], ["L1", "L2", "hoist", "L3"])
        self.assertTrue(all(c.passed for c in v.comparisons), [c.first_difference for c in v.comparisons])

    def test_mixed_profitable_unprofitable_and_fallback_accesses(self):
        v = self.check_program(MIXED)
        self.assertEqual(v.verdict, "PASS")

    def test_non_linear_fallback_program(self):
        self.assertEqual(self.check_program(FALLBACK).verdict, "PASS")

    def test_zero_trip_program(self):
        v = self.check_program(ZERO_TRIP)
        self.assertEqual(v.verdict, "PASS")

    def test_scalar_only_program(self):
        self.assertEqual(self.check_program("s = 1;\nt = s + 2;\n").verdict, "PASS")

    def test_every_example(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                v = verify(self.outcomes(path))
                self.assertEqual(v.verdict, "PASS", [c.first_difference for c in v.comparisons])

    def test_mixed_program_really_has_all_three_kinds_of_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "p.ml"
            path.write_text(MIXED, encoding="utf-8")
            entries = run_file(path, "L1", "L3").optimized.entries
        kinds = {(e.levels is None, e.applied) for e in entries}
        self.assertEqual(kinds, {(True, False), (False, True), (False, False)})

    def test_a_deliberately_wrong_optimization_is_detected(self):
        """Sabotage the L3 init constant by one: the trace (and the scalar) must differ from L0."""
        real = optimize_stage

        def sabotage(l2, stage):
            result = real(l2, stage)
            quads = list(result.code.quads)
            at = next(i for i, q in enumerate(quads) if q.op == ":=" and q.tag == "ADDR")
            quads[at] = dataclasses.replace(quads[at], arg1=quads[at].arg1 + 1)
            code = dataclasses.replace(result.code, quads=quads)
            return StageResult(stage, dataclasses.replace(result.program, code=code))

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "p.ml"
            path.write_text(ARRAY, encoding="utf-8")
            outs = self.outcomes(path)
            with mock.patch.object(driver, "optimize_stage", side_effect=sabotage):
                outs[-1] = LevelOutcome("L3", run_file(path, "L1", "L3"))
        v = verify(outs)
        self.assertEqual(v.verdict, "FAIL")
        bad = v.comparisons[-1]
        self.assertEqual(bad.level, "L3")
        self.assertFalse(bad.checks[2].passed)
        self.assertTrue(bad.first_difference.startswith("scalar s differs"))
        self.assertEqual(bad.checks[2].difference, "address trace differs at access 1: L0 (R, 1020) vs L3 (R, 1021)")
        self.assertTrue(all(c.passed for c in v.comparisons[:-1]))


class CliCompareTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(pathlib.Path(self._tmp.name) / "p.ml")
        pathlib.Path(self.path).write_text(ARRAY, encoding="utf-8")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def tampered(self, level, *, scalars=None, memory=None, trace=None):
        """A ``run_file`` replacement whose result for ``level`` has been altered."""

        def fake(path, baseline, stage):
            result = run_file(path, baseline, stage)
            if (baseline, stage) != RUNS[level]:
                return result
            run = result.run
            run = dataclasses.replace(
                run,
                scalars=run.scalars if scalars is None else scalars,
                memory=run.memory if memory is None else memory,
                trace=run.trace if trace is None else trace,
            )
            return dataclasses.replace(result, run=run)

        return mock.patch.object(cli, "run_file", side_effect=fake)

    def raising(self, level, error):
        def fake(path, baseline, stage):
            if (baseline, stage) == RUNS[level]:
                raise error
            return run_file(path, baseline, stage)

        return mock.patch.object(cli, "run_file", side_effect=fake)

    def test_pass_block_and_exit_zero(self):
        code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.count("PASS"), 5)  # four rows and the verdict line
        self.assertTrue(out.rstrip().endswith("verdict     : PASS"))

    def test_scalar_mismatch_fails_with_the_first_difference(self):
        real = run_file(self.path, "L1", "L2").run.scalars
        with self.tampered("L2", scalars={**real, "s": real["s"] + 1}):
            code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, err), (3, ""))
        self.assertRegex(out, rf" L0 vs L2 +: scalar s differs: L0 {real['s']} vs L2 {real['s'] + 1} +FAIL")
        self.assertTrue(out.rstrip().endswith("verdict     : FAIL"))
        self.assertRegex(out, r" L0 vs L1 +: scalars equal, memory equal, address trace equal +PASS")

    def test_memory_mismatch(self):
        memory = dict(run_file(self.path, "L1", "L3").run.memory)
        address = min(memory)
        memory[address] += 1
        with self.tampered("L3", memory=memory):
            code, out, _ = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual(code, 3)
        self.assertIn(f"memory differs at address {address}:", out)
        self.assertIn(" L0 vs L3 ", out)

    def test_trace_mismatch(self):
        trace = list(run_file(self.path, "L1", None).run.trace)
        trace[4] = ("R", trace[4][1] + 1)
        with self.tampered("hoist", trace=trace):
            code, out, _ = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual(code, 3)
        self.assertIn("address trace differs at access 5:", out)
        self.assertRegex(out, r" L0 vs hoist +: address trace differs at access 5")

    def test_only_failing_rows_show_a_difference(self):
        trace = [("R", 0)]
        with self.tampered("L1", trace=trace):
            out = self.cli(self.path, "--level", "all", "--compare")[1]
        self.assertEqual(out.count("FAIL"), 2)  # the L1 row and the verdict
        self.assertEqual(out.count("PASS"), 3)

    def test_runtime_error_in_a_level_fails_the_verification(self):
        error = RuntimeAccessError("runtime error: level L2, quadruple 4: bad address", 4, "L2")
        with self.raising("L2", error):
            code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, err), (3, ""))
        self.assertRegex(out, r" L0 vs L2 +: runtime error at quadruple 4 where L0 completed +FAIL")
        self.assertRegex(out, r" L0 vs L3 +: scalars equal, memory equal, address trace equal +PASS")
        self.assertIn("== Run L3", out)  # the completed levels are still shown
        self.assertNotIn("== Run L2", out)

    def test_step_limit_error_in_a_level_fails_the_verification(self):
        with self.raising("L3", StepLimitError("runtime error: step limit exceeded")):
            code, out, _ = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual(code, 3)
        self.assertRegex(out, r" L0 vs L3 +: step limit exceeded where L0 completed +FAIL")

    def test_baseline_runtime_error_exits_2_without_verification(self):
        error = RuntimeAccessError("runtime error: level L0, quadruple 7: bad address", 7, "L0")
        with self.raising("L0", error):
            code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("runtime error:", err)
        self.assertEqual(err.count("\n"), 1)

    def test_internal_error_exits_4_not_a_verdict(self):
        error = InternalError("internal error: T3 read before assignment", 3, "L2")
        with self.raising("L2", error):
            code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, out), (4, ""))
        self.assertIn("internal error:", err)
        self.assertNotIn("Verification", out)

    def test_unexpected_errors_in_compare_mode_are_internal_errors(self):
        with self.raising("L2", RuntimeError("bug")):
            code, out, err = self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual((code, out), (4, ""))
        self.assertEqual(err, "internal error: unexpected RuntimeError: bug\n")

    def test_compare_with_show_flags_for_a_failing_level(self):
        error = RuntimeAccessError("runtime error: x", 4, "L3")
        with self.raising("L3", error):
            code, out, _ = self.cli(self.path, "--level", "all", "--compare", "--show-vectors", "--show-quads",
                                    "--show-plan", "--run")
        self.assertEqual(code, 3)
        self.assertIn("== Quadruples (L2)", out)
        self.assertNotIn("== Quadruples (L3)", out)
        self.assertIn("no optimization plan: level L3 did not complete", out)

    def test_compare_still_requires_level_all(self):
        code, out, err = self.cli(self.path, "--level", "L3", "--compare")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--compare requires --level all", err)

    def test_compare_runs_every_level_exactly_once(self):
        with mock.patch.object(cli, "run_file", wraps=run_file) as spy:
            self.cli(self.path, "--level", "all", "--compare")
        self.assertEqual([c.args[1:] for c in spy.call_args_list], [RUNS[n] for n in LEVELS])

    def test_zero_trip_and_fallback_programs_verify_through_the_cli(self):
        for text in (ZERO_TRIP, FALLBACK, MIXED):
            with self.subTest(text=text[:30]):
                pathlib.Path(self.path).write_text(text, encoding="utf-8")
                code, out, err = self.cli(self.path, "--level", "all", "--compare")
                self.assertEqual((code, err), (0, ""))
                self.assertTrue(out.rstrip().endswith("verdict     : PASS"))

    # -- behaviour without --compare is unchanged -------------------------------

    def test_without_compare_a_runtime_error_still_aborts_everything(self):
        error = RuntimeAccessError("runtime error: level L2, quadruple 4: bad address", 4, "L2")
        with self.raising("L2", error):
            code, out, err = self.cli(self.path, "--level", "all", "--run")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("runtime error:", err)

    def test_without_compare_there_is_no_verification_block(self):
        code, out, _ = self.cli(self.path, "--level", "all", "--run")
        self.assertEqual(code, 0)
        self.assertNotIn("Verification", out)
        self.assertNotIn("verdict", out)

    def test_without_compare_output_is_identical_to_before(self):
        code, out, _ = self.cli(self.path, "--level", "L3")
        expected = f"{format_header('Run L3')}\n{run_file(self.path, 'L1', 'L3').run.format()}\n"
        self.assertEqual((code, out), (0, expected))

    def test_verification_block_is_printed_once_per_run(self):
        out = self.cli(self.path, "--level", "all", "--compare")[1]
        self.assertEqual(out.count("== Verification"), 1)
        self.assertEqual(self.cli(self.path, "--level", "all", "--compare")[1], out)  # deterministic


class FormatTests(unittest.TestCase):
    def test_format_of_a_failing_row_replaces_the_descriptive_text(self):
        failing = compare_runs("L0", make_run(), "hoist", make_run(scalars={"s": 6}))
        passing = compare_runs("L0", make_run(), "L1", make_run())
        text = format_verification(Verification("L0", (passing, failing)))
        lines = text.split("\n")
        self.assertTrue(lines[0].startswith("== Verification ="))
        self.assertRegex(lines[1], r"^ L0 vs L1    : scalars equal, memory equal, address trace equal   PASS$")
        self.assertRegex(lines[2], r"^ L0 vs hoist : scalar s differs: L0 5 vs hoist 6 +FAIL$")
        self.assertRegex(lines[3], r"^ verdict     : FAIL$")

    def test_formatting_is_pure(self):
        v = Verification("L0", (compare_runs("L0", make_run(), "L2", make_run()),))
        self.assertEqual(format_verification(v), format_verification(v))

    def test_check_result_names_are_the_documented_ones(self):
        self.assertEqual(CHECKS, ("scalars", "memory", "address trace"))
        self.assertEqual(CheckResult("scalars", True, None).difference, None)


class ErrorHierarchyTests(unittest.TestCase):
    def test_execution_errors_are_project_errors(self):
        for cls in (RuntimeAccessError, StepLimitError, InternalError):
            self.assertTrue(issubclass(cls, ArrayPeepError))


if __name__ == "__main__":
    unittest.main()
