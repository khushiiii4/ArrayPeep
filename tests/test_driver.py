"""Tests for arraypeep.driver (Phase 10.1: the pipeline driver).

Expected values are hand-derived: ``s = 1 + 2 + 3`` and the traces of the
roadmap examples (A[1:10,1:20]@1000 identity traversal 1000..1199).
"""
import pathlib
import tempfile
import unittest
from unittest import mock

from arraypeep import driver
from arraypeep.codegen import GeneratedCode, generate
from arraypeep.driver import DriverError, PipelineResult, run_file, run_source
from arraypeep.interpreter import RunResult, RuntimeAccessError, execute, init_value
from arraypeep.lexer import MiniLoopSyntaxError, Token
from arraypeep.loop_opt import STAGES, GuardedProgram, HoistedProgram, StageError, StageResult, optimize_stage
from arraypeep.optimizer import L2Program, optimize_l2
from arraypeep.parser import parse
from arraypeep.semantic import SemanticError, analyze
from arraypeep.symbol_table import ArrayPeepError

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))

SCALAR = "s = 0;\ns = s + 5 * 2;\n"
LOOP = "s = 0;\nfor i = 1 to 3 do s = s + i; end\n"
ARRAY = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end\n"
OFFSETS = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end\n"
MIXED = (
    "array A[1:10, 1:20] at 1000; array V[0:9] at 100; array W[0:99] at 2000;\n"
    "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j] + V[i] + A[3, 4] + W[i * j]; end end\n"
)


def real(scalars):
    return {k: v for k, v in scalars.items() if not (k.startswith("T") or k.startswith("P"))}


class ValidProgramTests(unittest.TestCase):
    def test_scalar_program(self):
        r = run_source(SCALAR)
        self.assertIsInstance(r, PipelineResult)
        self.assertEqual(r.run.scalars["s"], 10)
        self.assertEqual(r.run.trace, [])
        self.assertEqual(len(r.symtab.arrays), 0)
        self.assertEqual((r.stage, r.baseline_level), (None, "L1"))
        self.assertEqual(r.source, SCALAR)
        self.assertIsNone(r.source_path)

    def test_loop_program(self):
        r = run_source(LOOP)
        self.assertEqual(r.run.scalars["s"], 6)
        self.assertEqual([q.op for q in r.code.quads if q.op in ("FOR", "ENDFOR")], ["FOR", "ENDFOR"])

    def test_array_program(self):
        r = run_source(ARRAY)
        self.assertEqual([a for _, a in r.run.trace], list(range(1000, 1200)))
        self.assertEqual({k for k, _ in r.run.trace}, {"R"})
        self.assertEqual(r.run.scalars["s"], sum(init_value(a) for a in range(1000, 1200)))
        (info,) = r.symtab.arrays
        self.assertEqual((info.name, info.conspart), ("A", 979))

    def test_result_exposes_every_intermediate(self):
        r = run_source(ARRAY, "L0", "L3", path="x.ml")
        self.assertTrue(all(isinstance(t, Token) for t in r.tokens))
        self.assertIsInstance(r.tokens, tuple)
        self.assertEqual(len(r.program.items), 2)
        self.assertIsInstance(r.baseline, GeneratedCode)
        self.assertEqual(r.baseline.level, "L0")
        self.assertIsInstance(r.l2, L2Program)
        self.assertIsInstance(r.optimized, StageResult)
        self.assertIs(r.code, r.optimized.code)
        self.assertIsInstance(r.run, RunResult)
        self.assertEqual(r.source_path, "x.ml")

    def test_result_is_frozen(self):
        r = run_source(SCALAR)
        with self.assertRaises(Exception):
            r.stage = "L3"  # type: ignore[misc]

    def test_empty_program(self):
        r = run_source("")
        self.assertEqual((r.code.quads, r.run.scalars, r.run.trace), ([], {}, []))


class BaselineAndStageTests(unittest.TestCase):
    def test_no_stage_runs_the_baseline_listing(self):
        for level in ("L0", "L1"):
            with self.subTest(level=level):
                r = run_source(ARRAY, level)
                self.assertIsNone(r.stage)
                self.assertIsNone(r.l2)
                self.assertIsNone(r.optimized)
                self.assertIs(r.code, r.baseline)
                self.assertEqual(r.code.level, level)
                self.assertEqual(r.run.level, level)

    def test_each_stage_selects_the_right_result_type(self):
        kinds = {"L2": L2Program, "hoist": HoistedProgram, "L3": GuardedProgram}
        for stage in STAGES:
            with self.subTest(stage=stage):
                r = run_source(OFFSETS, "L1", stage)
                self.assertEqual(r.stage, stage)
                self.assertEqual(r.optimized.stage, stage)
                self.assertIs(type(r.optimized.program), kinds[stage])
                self.assertEqual(r.run.level, r.code.level)

    def test_driver_passes_the_selected_stage_to_the_stage_selector(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                with mock.patch.object(driver, "optimize_stage", wraps=optimize_stage) as spy:
                    r = run_source(ARRAY, "L1", stage)
                spy.assert_called_once()
                args = spy.call_args.args
                self.assertEqual(args[1], stage)
                self.assertIsInstance(args[0], L2Program)
                self.assertIs(args[0], r.l2)

    def test_no_stage_does_not_call_the_optimizer(self):
        with mock.patch.object(driver, "optimize_stage") as stage_spy, mock.patch.object(
            driver, "optimize_l2"
        ) as l2_spy:
            run_source(ARRAY)
        stage_spy.assert_not_called()
        l2_spy.assert_not_called()

    def test_l2_program_is_built_from_the_baseline_listing(self):
        with mock.patch.object(driver, "optimize_l2", wraps=optimize_l2) as spy:
            r = run_source(ARRAY, "L0", "L2")
        code, table = spy.call_args.args
        self.assertIs(code, r.baseline)
        self.assertIs(table, r.symtab)

    def test_baseline_level_reaches_the_code_generator(self):
        with mock.patch.object(driver, "generate", wraps=generate) as spy:
            run_source(ARRAY, "L0")
        self.assertEqual(spy.call_args.args[2], "L0")

    def test_all_stages_and_levels_have_equal_behaviour(self):
        for name, src in (("array", ARRAY), ("offsets", OFFSETS), ("mixed", MIXED), ("loop", LOOP), ("scalar", SCALAR)):
            base = run_source(src, "L0")
            for level in ("L0", "L1"):
                for stage in (None, *STAGES):
                    with self.subTest(program=name, level=level, stage=stage):
                        r = run_source(src, level, stage)
                        self.assertEqual(real(r.run.scalars), real(base.run.scalars))
                        self.assertEqual(r.run.memory, base.run.memory)
                        self.assertEqual(r.run.trace, base.run.trace)

    def test_stage_changes_the_listing_but_not_the_baseline(self):
        plain = run_source(OFFSETS)
        l3 = run_source(OFFSETS, "L1", "L3")
        self.assertEqual(l3.baseline.quads, plain.baseline.quads)
        self.assertNotEqual(l3.code.quads, plain.code.quads)
        self.assertEqual(l3.run.counters.multiplications, 0)
        self.assertGreater(plain.run.counters.multiplications, 0)

    def test_mixed_program_keeps_per_access_decisions(self):
        r = run_source(MIXED, "L1", "L3")
        decisions = [(e.l2.report.source, e.applied) for e in r.optimized.entries]
        self.assertEqual(decisions, [("A[i, j]", True), ("V[i]", False), ("A[3, 4]", False), ("W[i * j]", False)])


class ExistingResultsUnalteredTests(unittest.TestCase):
    def test_run_equals_direct_interpretation_of_the_returned_code(self):
        for stage in (None, *STAGES):
            with self.subTest(stage=stage):
                r = run_source(OFFSETS, "L1", stage)
                direct = execute(r.code.quads, r.symtab.arrays, r.code.level)
                self.assertEqual(
                    (r.run.scalars, r.run.memory, r.run.trace, r.run.counters),
                    (direct.scalars, direct.memory, direct.trace, direct.counters),
                )

    def test_pipeline_matches_a_manual_run_of_the_same_stages(self):
        program = parse(OFFSETS)
        table = analyze(program)
        l2 = optimize_l2(generate(program, table, "L1"), table)
        for stage in STAGES:
            with self.subTest(stage=stage):
                manual = optimize_stage(l2, stage)
                r = run_source(OFFSETS, "L1", stage)
                self.assertEqual(r.code.quads, manual.code.quads)
                self.assertEqual(r.code.accesses, manual.code.accesses)
                self.assertEqual(r.code.loops, manual.code.loops)
                direct = execute(manual.code.quads, table.arrays, manual.code.level)
                self.assertEqual((r.run.scalars, r.run.memory, r.run.trace), (direct.scalars, direct.memory, direct.trace))

    def test_optimizer_inputs_are_not_modified(self):
        r = run_source(ARRAY, "L1", "L3")
        again = run_source(ARRAY, "L1", None)
        self.assertEqual(r.baseline.quads, again.baseline.quads)
        before = (list(r.l2.code.quads), list(r.l2.code.accesses), list(r.l2.code.loops))
        optimize_stage(r.l2, "L3")
        self.assertEqual((r.l2.code.quads, r.l2.code.accesses, r.l2.code.loops), before)

    def test_repeated_runs_are_deterministic(self):
        a, b = run_source(OFFSETS, "L1", "L3"), run_source(OFFSETS, "L1", "L3")
        self.assertEqual(a.code.quads, b.code.quads)
        self.assertEqual((a.run.scalars, a.run.memory, a.run.trace, a.run.counters),
                         (b.run.scalars, b.run.memory, b.run.trace, b.run.counters))

    def test_driver_has_no_stage_logic_of_its_own(self):
        # the optimizer result is exactly what optimize_stage returned
        sentinel = StageResult("L2", mock.Mock(code=mock.Mock(quads=[], level="L2", spec=["quads", "level"])))
        with mock.patch.object(driver, "optimize_stage", return_value=sentinel), mock.patch.object(
            driver, "execute", return_value="RUN"
        ) as run:
            r = run_source(ARRAY, "L1", "L2")
        self.assertIs(r.optimized, sentinel)
        self.assertIs(r.code, sentinel.code)
        self.assertEqual(r.run, "RUN")
        self.assertIs(run.call_args.args[0], sentinel.code.quads)


class InvalidInputTests(unittest.TestCase):
    def test_syntax_error_propagates_with_position(self):
        src = "s = 1\nt = 2;"
        with self.assertRaises(MiniLoopSyntaxError) as direct:
            parse(src)
        with self.assertRaises(MiniLoopSyntaxError) as piped:
            run_source(src)
        self.assertEqual((piped.exception.line, piped.exception.col), (direct.exception.line, direct.exception.col))
        self.assertEqual(str(piped.exception), str(direct.exception))
        self.assertEqual(piped.exception.line, 1)

    def test_illegal_character(self):
        with self.assertRaises(MiniLoopSyntaxError):
            run_source("s = 1 @ 2;")

    def test_semantic_error_propagates(self):
        with self.assertRaises(SemanticError):
            run_source("s = B[1];")
        with self.assertRaises(SemanticError):
            run_source("array A[1:3, 1:3] at 0;\ns = A[1];")
        with self.assertRaises(SemanticError):
            run_source("for i = 1 to 3 do i = 2; end")

    def test_declaration_error_propagates(self):
        with self.assertRaises(ArrayPeepError):
            run_source("array A[5:1] at 0;")

    def test_runtime_error_propagates(self):
        with self.assertRaises(RuntimeAccessError):
            run_source("array V[0:9] at 100;\ns = V[10];")

    def test_original_error_text_is_preserved(self):
        with self.assertRaises(SemanticError) as direct:
            analyze(parse("s = B[1];"))
        with self.assertRaises(SemanticError) as piped:
            run_source("s = B[1];")
        self.assertEqual(str(direct.exception), str(piped.exception))

    def test_errors_are_project_errors(self):
        for src in ("s = 1 @ 2;", "s = B[1];", "array V[0:9] at 100;\ns = V[10];"):
            with self.subTest(src=src):
                with self.assertRaises(ArrayPeepError):
                    run_source(src)

    def test_unknown_stage_is_rejected_before_any_work(self):
        for bad in ("L1", "l2", "", "hoisting", "L3-incr", 3, ["L3"]):
            with self.subTest(stage=bad):
                with mock.patch.object(driver, "tokenize") as lexer_spy:
                    with self.assertRaises(StageError) as ctx:
                        run_source(SCALAR, "L1", bad)  # type: ignore[arg-type]
                lexer_spy.assert_not_called()
                self.assertIn("expected one of: L2, hoist, L3", str(ctx.exception))

    def test_unsupported_baseline_level_is_rejected(self):
        for bad in ("L2", "L3", "l1", "", None, 1):
            with self.subTest(level=bad):
                with self.assertRaises(DriverError) as ctx:
                    run_source(SCALAR, bad)  # type: ignore[arg-type]
                self.assertIn("expected one of: L0, L1", str(ctx.exception))

    def test_driver_error_is_a_project_error(self):
        self.assertTrue(issubclass(DriverError, ArrayPeepError))

    def test_invalid_source_with_valid_options_still_fails_in_the_front_end(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                with self.assertRaises(SemanticError):
                    run_source("s = B[1];", "L0", stage)


class FileTests(unittest.TestCase):
    def test_run_file_reads_and_records_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prog.ml"
            path.write_text(LOOP, encoding="utf-8")
            for given in (path, str(path)):
                r = run_file(given)
                self.assertEqual(r.source, LOOP)
                self.assertEqual(r.source_path, str(given))
                self.assertEqual(r.run.scalars["s"], 6)

    def test_run_file_passes_level_and_stage_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prog.ml"
            path.write_text(ARRAY, encoding="utf-8")
            r = run_file(path, "L0", "hoist")
            self.assertEqual((r.baseline_level, r.stage), ("L0", "hoist"))
            self.assertIsInstance(r.optimized.program, HoistedProgram)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "nope.ml"
            with self.assertRaises(DriverError) as ctx:
                run_file(missing)
            self.assertIn("cannot read source file", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, OSError)

    def test_directory_instead_of_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DriverError):
                run_file(tmp)

    def test_undecodable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.ml"
            path.write_bytes(b"s = 1;\xff\xfe\n")
            with self.assertRaises(DriverError) as ctx:
                run_file(path)
            self.assertIsInstance(ctx.exception.__cause__, UnicodeDecodeError)

    def test_file_with_syntax_error_propagates_the_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.ml"
            path.write_text("s = ;", encoding="utf-8")
            with self.assertRaises(MiniLoopSyntaxError):
                run_file(path)

    def test_every_example_runs_at_every_baseline_and_stage(self):
        for path in EXAMPLES:
            base = run_file(path, "L0")
            for level in ("L0", "L1"):
                for stage in (None, *STAGES):
                    with self.subTest(example=path.name, level=level, stage=stage):
                        r = run_file(path, level, stage)
                        self.assertEqual(real(r.run.scalars), real(base.run.scalars))
                        self.assertEqual(r.run.memory, base.run.memory)
                        self.assertEqual(r.run.trace, base.run.trace)


class NoSideEffectsTests(unittest.TestCase):
    def test_driver_prints_nothing(self):
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            run_source(OFFSETS, "L1", "L3")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
