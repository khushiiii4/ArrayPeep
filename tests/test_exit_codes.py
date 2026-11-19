"""Tests for the Phase 10.5 error handling and exit codes (docs/language_spec.md Section 9).

    0  success, and every requested verification PASS
    1  unreadable file, syntax / semantic / declaration error, invalid command line
    2  run-time error while executing the program
    3  verification FAIL
    4  internal error (a compiler bug, or any unexpected exception)
"""
import contextlib
import dataclasses
import importlib
import inspect
import io
import pathlib
import pkgutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import arraypeep
from arraypeep import __main__ as cli
from arraypeep import driver
from arraypeep.codegen import CodegenError, generate
from arraypeep.driver import DriverError, run_file
from arraypeep.interpreter import (
    ExecutionError,
    InternalError,
    RuntimeAccessError,
    StepLimitError,
)
from arraypeep.lexer import MiniLoopSyntaxError
from arraypeep.loop_opt import HoistError, LoopLevelError, StageError
from arraypeep.optimizer import L2BuildError, WindowError
from arraypeep.quad import Quad
from arraypeep.semantic import SemanticError
from arraypeep.symbol_table import (
    ArrayPeepError,
    DeclarationError,
    DuplicateArrayError,
    NameConflictError,
    OverlapError,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = cli.LEVEL_RUNS

GOOD = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 9 do for j = 1 to 19 do s = s + A[i + 1, j]; end end\n"
OUT_OF_RANGE = "array V[0:9] at 100;\ns = V[12];\n"
SYNTAX = "s = 1 @ 2;\n"
SEMANTIC = "s = B[1];\n"
DECLARATION = "array A[5:1] at 0;\n"


class ExitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def source(self, text: str) -> str:
        self._count = getattr(self, "_count", 0) + 1
        path = self.dir / f"prog{self._count}.ml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    @staticmethod
    def cli(*argv: str):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def assert_single_line_error(self, err: str):
        self.assertEqual(err.count("\n"), 1, err)
        self.assertNotIn("Traceback", err)

    def tampered_run_file(self, level, **changes):
        def fake(path, baseline, stage):
            result = run_file(path, baseline, stage)
            if (baseline, stage) == RUNS[level]:
                result = dataclasses.replace(result, run=dataclasses.replace(result.run, **changes))
            return result

        return mock.patch.object(cli, "run_file", side_effect=fake)

    def raising(self, level, error):
        def fake(path, baseline, stage):
            if (baseline, stage) == RUNS[level]:
                raise error
            return run_file(path, baseline, stage)

        return mock.patch.object(cli, "run_file", side_effect=fake)


class ConstantsTests(unittest.TestCase):
    def test_exit_code_values_follow_the_spec(self):
        self.assertEqual(
            (cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_RUNTIME, cli.EXIT_VERIFICATION_FAILED, cli.EXIT_INTERNAL),
            (0, 1, 2, 3, 4),
        )


class ExitCodeZeroTests(ExitCase):
    def test_success(self):
        for argv in ([], ["--level", "L0"], ["--level", "all"], ["--level", "all", "--compare"]):
            with self.subTest(argv=argv):
                code, _, err = self.cli(self.source(GOOD), *argv)
                self.assertEqual((code, err), (0, ""))

    def test_help_exits_zero(self):
        self.assertEqual(self.cli("--help")[0], 0)


class ExitCodeOneTests(ExitCase):
    def test_syntax_error(self):
        code, out, err = self.cli(self.source(SYNTAX))
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"prog\d+\.ml: line 1, col 7: syntax error: illegal character '@'\n$")
        self.assert_single_line_error(err)

    def test_semantic_error(self):
        code, out, err = self.cli(self.source(SEMANTIC))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("line 1: semantic error:", err)
        self.assert_single_line_error(err)

    def test_declaration_error(self):
        code, out, err = self.cli(self.source(DECLARATION))
        self.assertEqual((code, out), (1, ""))
        self.assert_single_line_error(err)

    def test_missing_file(self):
        code, out, err = self.cli(str(self.dir / "nope.ml"))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("cannot read source file", err)
        self.assert_single_line_error(err)

    def test_directory_instead_of_file(self):
        code, _, err = self.cli(str(self.dir))
        self.assertEqual(code, 1)
        self.assert_single_line_error(err)

    def test_usage_errors_exit_one_not_two(self):
        path = self.source(GOOD)
        for argv in ([], [path, "--level", "L9"], [path, "--bogus"], [path, "--level"], ["--level", "L3"],
                     [path, "--level", "L3", "--compare"], [path, "--compare"]):
            with self.subTest(argv=argv):
                code, out, err = self.cli(*argv)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("usage: arraypeep", err)
                self.assertIn("arraypeep: error:", err)
                self.assertNotIn("Traceback", err)

    def test_usage_error_message_is_printed_once(self):
        err = self.cli(self.source(GOOD), "--level", "L9")[2]
        self.assertEqual(err.count("error:"), 1)
        self.assertEqual(err.count("usage:"), 1)

    def test_compare_requires_level_all_is_a_usage_error(self):
        code, _, err = self.cli(self.source(GOOD), "--compare")
        self.assertEqual(code, 1)
        self.assertIn("--compare requires --level all", err)

    def test_unsupported_stage_error_is_a_project_error(self):
        with mock.patch.object(cli, "run_file", side_effect=StageError("nope")):
            code, _, err = self.cli(self.source(GOOD))
        self.assertEqual(code, 1)
        self.assertIn("unknown optimization stage", err)

    def test_driver_error_and_codegen_error(self):
        for error in (DriverError("cannot read"), CodegenError("array 'Q' is not declared", 3)):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(cli, "run_file", side_effect=error):
                    code, out, err = self.cli(self.source(GOOD))
                self.assertEqual((code, out), (1, ""))
                self.assert_single_line_error(err)

    def test_no_partial_output_on_errors(self):
        for text in (SYNTAX, SEMANTIC, DECLARATION):
            self.assertEqual(self.cli(self.source(text), "--level", "all", "--run")[1], "")


class ExitCodeTwoTests(ExitCase):
    def test_runtime_error_without_compare(self):
        for level in ("L0", "L1", "L2", "hoist", "L3", "all"):
            with self.subTest(level=level):
                code, out, err = self.cli(self.source(OUT_OF_RANGE), "--level", level)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("runtime error: "), err)
                self.assertIn("outside every declared array", err)
                self.assert_single_line_error(err)

    def test_baseline_runtime_error_in_compare_mode(self):
        code, out, err = self.cli(self.source(OUT_OF_RANGE), "--level", "all", "--compare")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("runtime error: "))
        self.assert_single_line_error(err)
        self.assertNotIn("Verification", out)

    def test_baseline_step_limit_error(self):
        for argv in (["--level", "all"], ["--level", "all", "--compare"]):
            with self.subTest(argv=argv):
                with self.raising("L0", StepLimitError("runtime error: step limit of 10 executed quadruples exceeded")):
                    code, out, err = self.cli(self.source(GOOD), *argv)
                self.assertEqual((code, out), (2, ""))
                self.assert_single_line_error(err)

    def test_non_baseline_runtime_error_without_compare_is_exit_two(self):
        error = RuntimeAccessError("runtime error: level L2, quadruple 4: bad address", 4, "L2")
        with self.raising("L2", error):
            code, out, err = self.cli(self.source(GOOD), "--level", "all")
        self.assertEqual((code, out), (2, ""))
        self.assert_single_line_error(err)

    def test_runtime_error_message_is_not_prefixed_twice(self):
        err = self.cli(self.source(OUT_OF_RANGE))[2]
        self.assertEqual(err.count("runtime error:"), 1)
        self.assertNotIn("arraypeep: error:", err)


class ExitCodeThreeTests(ExitCase):
    def test_scalar_memory_and_trace_mismatches_fail_verification(self):
        real = run_file(self.source(GOOD), "L1", "L2").run
        cases = {
            "scalars": {"scalars": {**real.scalars, "s": real.scalars["s"] + 1}},
            "memory": {"memory": {**real.memory, min(real.memory): real.memory[min(real.memory)] + 1}},
            "trace": {"trace": real.trace[:-1]},
        }
        for name, changes in cases.items():
            with self.subTest(category=name):
                with self.tampered_run_file("L2", **changes):
                    code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
                self.assertEqual((code, err), (3, ""))
                self.assertTrue(out.split("== Metrics")[0].rstrip().endswith("verdict     : FAIL"))

    def test_runtime_error_in_a_non_baseline_level_is_a_verification_failure(self):
        for error in (RuntimeAccessError("runtime error: x", 4, "L3"), StepLimitError("runtime error: step limit")):
            with self.subTest(error=type(error).__name__):
                with self.raising("L3", error):
                    code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
                self.assertEqual((code, err), (3, ""))
                self.assertIn("where L0 completed", out)
                self.assertIn("verdict     : FAIL", out)

    def test_failure_exit_code_does_not_depend_on_show_flags(self):
        with self.tampered_run_file("hoist", trace=[]):
            code, _, _ = self.cli(self.source(GOOD), "--level", "all", "--compare", "--show-vectors", "--show-quads",
                                  "--show-plan", "--run")
        self.assertEqual(code, 3)

    def test_passing_verification_is_zero(self):
        self.assertEqual(self.cli(self.source(GOOD), "--level", "all", "--compare")[0], 0)

    def test_verification_failure_is_not_reported_without_compare(self):
        with self.tampered_run_file("L2", trace=[]):
            code, _, _ = self.cli(self.source(GOOD), "--level", "all")
        self.assertEqual(code, 0)


class ExitCodeFourTests(ExitCase):
    INTERNAL = (
        InternalError("internal error: T3 read before assignment", 3, "L2"),
        WindowError("window mismatch"),
        L2BuildError("bad L2 listing"),
        HoistError("bad hoisted listing"),
        LoopLevelError("plan differs from loops"),
    )

    def test_internal_error_classes(self):
        for error in self.INTERNAL:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(cli, "run_file", side_effect=error):
                    code, out, err = self.cli(self.source(GOOD))
                self.assertEqual((code, out), (4, ""))
                self.assertTrue(err.startswith("internal error: "), err)
                self.assertEqual(err.count("internal error:"), 1)  # the prefix is not doubled
                self.assert_single_line_error(err)

    def test_unexpected_exceptions_are_internal_errors(self):
        for error in (RuntimeError("bug"), ValueError("v"), TypeError("t"), KeyError("k"), AttributeError("a"),
                      ZeroDivisionError("z"), RecursionError("r"), OSError("o")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(cli, "run_file", side_effect=error):
                    code, out, err = self.cli(self.source(GOOD))
                self.assertEqual((code, out), (4, ""))
                self.assertEqual(err, f"internal error: unexpected {type(error).__name__}: {error}\n")

    def test_real_ill_formed_listing_is_an_internal_error(self):
        real_generate = generate

        def broken(program, symtab, level):
            code = real_generate(program, symtab, level)
            return dataclasses.replace(code, quads=[Quad("+", "T99", 1, "T98"), *code.quads])

        path = self.source(GOOD)
        with mock.patch.object(driver, "generate", side_effect=broken):
            # at L1 the interpreter itself rejects the listing; with an optimization stage the window check does
            for level, needle in (("L1", "T99"), ("L3", "quadruple 6")):
                with self.subTest(level=level):
                    code, out, err = self.cli(path, "--level", level)
                    self.assertEqual((code, out), (4, ""))
                    self.assertTrue(err.startswith("internal error: "))
                    self.assertIn(needle, err)
                    self.assert_single_line_error(err)

    def test_internal_error_in_a_non_baseline_level_in_compare_mode(self):
        error = InternalError("internal error: x", 1, "L2")
        with self.raising("L2", error):
            code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
        self.assertEqual((code, out), (4, ""))
        self.assertNotIn("Verification", out)
        self.assert_single_line_error(err)

    def test_internal_error_in_the_baseline_in_compare_mode(self):
        with self.raising("L0", InternalError("internal error: y", 1, "L0")):
            code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
        self.assertEqual((code, out), (4, ""))

    def test_unexpected_exception_in_compare_mode(self):
        with self.raising("L2", RuntimeError("bug")):
            code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
        self.assertEqual((code, out), (4, ""))
        self.assertEqual(err, "internal error: unexpected RuntimeError: bug\n")

    def test_unexpected_exception_while_formatting(self):
        with mock.patch.object(cli, "format_run", side_effect=RuntimeError("fmt")):
            code, out, err = self.cli(self.source(GOOD))
        self.assertEqual((code, out), (4, ""))
        self.assertIn("unexpected RuntimeError: fmt", err)

    def test_a_bare_execution_error_is_treated_as_internal(self):
        with mock.patch.object(cli, "run_file", side_effect=ExecutionError("odd")):
            self.assertEqual(self.cli(self.source(GOOD))[0], 4)


class CompareDistinguishesTests(ExitCase):
    """In --compare mode a verification FAIL (3), a baseline run-time error (2) and an internal error (4) differ."""

    def test_the_four_outcomes_have_four_different_codes(self):
        path = self.source(GOOD)
        args = ("--level", "all", "--compare")
        codes = {}
        codes["pass"] = self.cli(path, *args)[0]
        with self.tampered_run_file("L2", trace=[]):
            codes["verification fail"] = self.cli(path, *args)[0]
        with self.raising("L0", RuntimeAccessError("runtime error: bad", 1, "L0")):
            codes["baseline runtime"] = self.cli(path, *args)[0]
        with self.raising("L2", InternalError("internal error: bad", 1, "L2")):
            codes["internal"] = self.cli(path, *args)[0]
        self.assertEqual(codes, {"pass": 0, "verification fail": 3, "baseline runtime": 2, "internal": 4})

    def test_non_baseline_runtime_error_is_a_verdict_not_an_error(self):
        with self.raising("L3", RuntimeAccessError("runtime error: bad", 1, "L3")):
            code, out, err = self.cli(self.source(GOOD), "--level", "all", "--compare")
        self.assertEqual((code, err), (3, ""))
        self.assertIn("== Verification", out)

    def test_syntax_error_in_compare_mode_is_still_exit_one(self):
        self.assertEqual(self.cli(self.source(SYNTAX), "--level", "all", "--compare")[0], 1)


class ProcessControlTests(ExitCase):
    def test_keyboard_interrupt_is_not_caught(self):
        with mock.patch.object(cli, "run_file", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cli(self.source(GOOD))

    def test_system_exit_from_the_pipeline_is_not_caught(self):
        with mock.patch.object(cli, "run_file", side_effect=SystemExit(7)):
            with self.assertRaises(SystemExit) as ctx:
                self.cli(self.source(GOOD))
        self.assertEqual(ctx.exception.code, 7)

    def test_generator_exit_is_not_caught(self):
        with mock.patch.object(cli, "run_file", side_effect=GeneratorExit):
            with self.assertRaises(GeneratorExit):
                self.cli(self.source(GOOD))


class ExitCodeClassificationTests(unittest.TestCase):
    EXPECTED = {
        MiniLoopSyntaxError: 1, SemanticError: 1, DeclarationError: 1, DuplicateArrayError: 1, OverlapError: 1,
        NameConflictError: 1, CodegenError: 1, DriverError: 1, StageError: 1,
        RuntimeAccessError: 2, StepLimitError: 2,
        InternalError: 4, WindowError: 4, L2BuildError: 4, HoistError: 4, LoopLevelError: 4,
        ExecutionError: 4,
    }

    @staticmethod
    def project_exception_classes():
        found = {}
        for info in pkgutil.walk_packages(arraypeep.__path__, "arraypeep."):
            if info.name.endswith("__main__"):
                continue
            module = importlib.import_module(info.name)
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ == module.__name__ and issubclass(cls, BaseException):
                    found[cls] = module.__name__
        return found

    def test_every_project_exception_derives_from_the_common_base(self):
        found = self.project_exception_classes()
        self.assertGreaterEqual(len(found), 17)
        outside = [f"{module}.{cls.__name__}" for cls, module in found.items() if not issubclass(cls, ArrayPeepError)]
        self.assertEqual(outside, [])

    def test_every_project_error_has_an_exit_code_decision(self):
        found = {cls for cls in self.project_exception_classes() if cls is not ArrayPeepError}
        self.assertEqual(found, set(self.EXPECTED), "a new error class needs an entry in the exit-code table")

    def test_exit_code_of_every_project_error(self):
        for cls, code in self.EXPECTED.items():
            with self.subTest(error=cls.__name__):
                error = cls.__new__(cls)  # the classification only looks at the type; constructors differ
                self.assertEqual(cli.exit_code(error), code)

    def test_the_base_error_itself_is_a_plain_error(self):
        self.assertEqual(cli.exit_code(ArrayPeepError("x")), 1)

    def test_error_hierarchy_keeps_meaningful_types(self):
        self.assertTrue(issubclass(DuplicateArrayError, DeclarationError))
        self.assertTrue(issubclass(OverlapError, DeclarationError))
        for cls in (RuntimeAccessError, StepLimitError, InternalError):
            self.assertTrue(issubclass(cls, ExecutionError))

    def test_messages_and_attributes_are_preserved(self):
        error = MiniLoopSyntaxError("expected ';'", 4, 12)
        self.assertEqual((error.message, error.line, error.col), ("expected ';'", 4, 12))
        self.assertEqual(error.format("prog.ml"), "prog.ml: line 4, col 12: syntax error: expected ';'")
        runtime = RuntimeAccessError("runtime error: x", 5, "L1")
        self.assertEqual((runtime.position, runtime.level, str(runtime)), (5, "L1", "runtime error: x"))

    def test_internal_prefix_is_present_on_internal_classes(self):
        for cls in (WindowError, L2BuildError, HoistError, LoopLevelError):
            self.assertTrue(str(cls("something")).startswith("internal error: "), cls.__name__)


class SubprocessTests(ExitCase):
    def run_module(self, *argv: str):
        return subprocess.run([sys.executable, "-m", "arraypeep", *argv], cwd=ROOT, capture_output=True, text=True,
                              timeout=60)

    def test_exit_codes_of_the_real_command(self):
        path = self.source(GOOD)
        table = [
            ((path,), 0),
            ((self.source(SYNTAX),), 1),
            ((str(self.dir / "missing.ml"),), 1),
            ((path, "--level", "L9"), 1),
            ((self.source(OUT_OF_RANGE),), 2),
            ((self.source(OUT_OF_RANGE), "--level", "all", "--compare"), 2),
            ((path, "--level", "all", "--compare"), 0),
        ]
        for argv, expected in table:
            with self.subTest(argv=argv[1:]):
                done = self.run_module(*argv)
                self.assertEqual(done.returncode, expected, done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                if expected:
                    self.assertEqual(done.stdout, "")

    def test_ordinary_errors_print_no_traceback_and_one_message(self):
        for text, needle in ((SYNTAX, "syntax error"), (SEMANTIC, "semantic error"), (OUT_OF_RANGE, "runtime error")):
            with self.subTest(needle=needle):
                done = self.run_module(self.source(text))
                self.assertEqual(done.stderr.count("\n"), 1, done.stderr)
                self.assertEqual(done.stderr.count(needle), 1)

    def test_usage_error_goes_to_stderr_with_exit_one(self):
        done = self.run_module(self.source(GOOD), "--nope")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertIn("usage: arraypeep", done.stderr)


if __name__ == "__main__":
    unittest.main()
