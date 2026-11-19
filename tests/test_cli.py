"""Tests for arraypeep.__main__ (Phase 10.2: the command-line interface).

Expected outputs are built from the existing formatters, so these tests
check that the CLI wires them up, not how they format.
"""
import contextlib
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from arraypeep import __main__ as cli
from arraypeep.driver import run_file
from arraypeep.loop_opt import format_l3_report
from arraypeep.optimizer import format_l2_report
from arraypeep.report import format_quadruple_listing, format_run
from arraypeep.symbol_table import format_information_vectors

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))

ARRAY = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end\n"
SCALAR = "s = 0;\nfor i = 1 to 3 do s = s + i; end\n"
LEVELS = ("L0", "L1", "L2", "hoist", "L3")
RUNS = {"L0": ("L0", None), "L1": ("L1", None), "L2": ("L1", "L2"), "hoist": ("L1", "hoist"), "L3": ("L1", "L3")}


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def source(self, text: str, name: str = "prog.ml") -> str:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    @staticmethod
    def cli(*argv: str):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


def run_block(path: str, level: str) -> str:
    return format_run(level, run_file(path, *RUNS[level]).run)


class ParserTests(unittest.TestCase):
    def test_defaults(self):
        args = cli.build_parser().parse_args(["prog.ml"])
        self.assertEqual(args.file, "prog.ml")
        self.assertEqual(args.level, "L3")
        self.assertFalse(any((args.show_vectors, args.show_quads, args.show_plan, args.run, args.compare)))

    def test_level_choices_are_exactly_the_approved_ones(self):
        self.assertEqual(cli.LEVEL_CHOICES, ("L0", "L1", "L2", "hoist", "L3", "all"))
        for level in cli.LEVEL_CHOICES:
            self.assertEqual(cli.build_parser().parse_args(["f.ml", "--level", level]).level, level)

    def test_flags(self):
        args = cli.build_parser().parse_args(
            ["f.ml", "--show-vectors", "--show-quads", "--show-plan", "--run", "--compare", "--level", "all"])
        self.assertTrue(all((args.show_vectors, args.show_quads, args.show_plan, args.run, args.compare)))

    def test_program_name_and_help_mention_every_option(self):
        text = cli.build_parser().format_help()
        for option in ("--level", "--show-vectors", "--show-quads", "--show-plan", "--run", "--compare", "hoist"):
            self.assertIn(option, text)
        self.assertTrue(text.startswith("usage: arraypeep"))

    def test_level_mapping_table(self):
        self.assertEqual(cli.LEVEL_RUNS, RUNS)
        self.assertEqual(cli.ALL_LEVELS, LEVELS)


class UsageErrorTests(CliCase):
    def test_invalid_level_is_a_usage_error(self):
        code, out, err = self.cli(self.source(SCALAR), "--level", "L4")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("invalid choice", err)

    def test_old_style_level_names_are_rejected(self):
        for bad in ("l3", "L2H", "ALL", "L3-hoist", ""):
            with self.subTest(level=bad):
                self.assertEqual(self.cli(self.source(SCALAR), "--level", bad)[0], 1)

    def test_missing_file_argument(self):
        code, out, err = self.cli()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("arguments are required", err)

    def test_unknown_option(self):
        code, _, err = self.cli(self.source(SCALAR), "--frobnicate")
        self.assertEqual(code, 1)
        self.assertIn("unrecognized arguments", err)

    def test_help_exits_zero(self):
        code, out, _ = self.cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("--compare", out)

    def test_usage_errors_do_not_run_the_pipeline(self):
        with mock.patch.object(cli, "run_file") as spy:
            self.cli("f.ml", "--level", "L9")
            self.cli("f.ml", "--compare")
        spy.assert_not_called()


class LevelMappingTests(CliCase):
    def test_each_level_calls_run_file_with_its_baseline_and_stage(self):
        path = self.source(ARRAY)
        for level, (baseline, stage) in RUNS.items():
            with self.subTest(level=level):
                with mock.patch.object(cli, "run_file", wraps=run_file) as spy:
                    code, _, _ = self.cli(path, "--level", level)
                self.assertEqual(code, 0)
                spy.assert_called_once_with(path, baseline, stage)

    def test_all_runs_five_levels_in_order(self):
        path = self.source(ARRAY)
        with mock.patch.object(cli, "run_file", wraps=run_file) as spy:
            code, out, _ = self.cli(path, "--level", "all")
        self.assertEqual(code, 0)
        self.assertEqual([c.args for c in spy.call_args_list], [(path, *RUNS[level]) for level in LEVELS])
        positions = [out.index(f"Run {level} ==") for level in LEVELS]
        self.assertEqual(positions, sorted(positions))

    def test_default_level_is_l3(self):
        path = self.source(ARRAY)
        with mock.patch.object(cli, "run_file", wraps=run_file) as spy:
            self.cli(path)
        spy.assert_called_once_with(path, "L1", "L3")

    def test_single_level_runs_exactly_once(self):
        path = self.source(ARRAY)
        with mock.patch.object(cli, "run_file", wraps=run_file) as spy:
            self.cli(path, "--level", "L2", "--show-quads", "--show-plan", "--run")
        self.assertEqual(spy.call_count, 1)


class OutputFlagTests(CliCase):
    def test_default_behaves_like_run(self):
        path = self.source(ARRAY)
        code, out, err = self.cli(path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, run_block(path, "L3") + "\n")
        self.assertEqual(self.cli(path, "--run"), (0, out, ""))

    def test_run_output_of_every_level(self):
        path = self.source(ARRAY)
        for level in LEVELS:
            with self.subTest(level=level):
                self.assertEqual(self.cli(path, "--level", level, "--run")[1], run_block(path, level) + "\n")

    def test_levels_agree_on_the_computed_value(self):
        path = self.source(ARRAY)
        values = {run_file(path, *RUNS[level]).run.scalars["s"] for level in LEVELS}
        self.assertEqual(len(values), 1)

    def test_show_vectors(self):
        path = self.source(ARRAY)
        code, out, _ = self.cli(path, "--show-vectors")
        self.assertEqual(code, 0)
        self.assertEqual(out, format_information_vectors(run_file(path).symtab.arrays) + "\n")
        self.assertIn("CONSPART", out)
        self.assertNotIn("== Run", out)  # a show flag disables the default run

    def test_show_vectors_without_arrays(self):
        out = self.cli(self.source(SCALAR), "--show-vectors")[1]
        self.assertIn("no arrays declared", out)

    def test_show_vectors_prints_once_for_all(self):
        path = self.source(ARRAY)
        out = self.cli(path, "--level", "all", "--show-vectors")[1]
        self.assertEqual(out.count("CONSPART"), 1)

    def test_show_quads_single_level(self):
        path = self.source(ARRAY)
        for level in LEVELS:
            with self.subTest(level=level):
                code, out, _ = self.cli(path, "--level", level, "--show-quads")
                expected = format_quadruple_listing(run_file(path, *RUNS[level]).code.quads, level)
                self.assertEqual((code, out), (0, expected + "\n"))

    def test_show_quads_all_levels_in_order(self):
        path = self.source(ARRAY)
        out = self.cli(path, "--level", "all", "--show-quads")[1]
        titles = [out.index(f"== Quadruples ({level}) =") for level in LEVELS]
        self.assertEqual(titles, sorted(titles))
        for level in LEVELS:
            self.assertIn(format_quadruple_listing(run_file(path, *RUNS[level]).code.quads, level), out)

    def test_optimized_listings_differ_from_the_baseline(self):
        path = self.source(ARRAY)
        listings = {level: self.cli(path, "--level", level, "--show-quads")[1] for level in LEVELS}
        for a, b in (("L0", "L1"), ("L1", "L2"), ("L2", "L3"), ("L1", "L3")):
            self.assertNotEqual(listings[a], listings[b], (a, b))

    def test_show_plan_l2_and_hoist_use_the_l2_report(self):
        path = self.source(ARRAY)
        for level in ("L2", "hoist"):
            with self.subTest(level=level):
                expected = format_l2_report(run_file(path, *RUNS[level]).l2)
                self.assertEqual(self.cli(path, "--level", level, "--show-plan")[1], expected + "\n")

    def test_show_plan_l3_uses_the_guarded_report(self):
        path = self.source(ARRAY)
        expected = format_l3_report(run_file(path, "L1", "L3").optimized.program)
        code, out, _ = self.cli(path, "--level", "L3", "--show-plan")
        self.assertEqual((code, out), (0, expected + "\n"))
        self.assertIn("L3 applied", out)

    def test_show_plan_all_prints_the_l3_report_once(self):
        path = self.source(ARRAY)
        expected = format_l3_report(run_file(path, "L1", "L3").optimized.program)
        out = self.cli(path, "--level", "all", "--show-plan")[1]
        self.assertEqual(out, expected + "\n")

    def test_show_plan_explains_when_none_exists(self):
        path = self.source(ARRAY)
        for level in ("L0", "L1"):
            with self.subTest(level=level):
                code, out, _ = self.cli(path, "--level", level, "--show-plan")
                self.assertEqual(code, 0)
                self.assertIn(f"no optimization plan at level {level}", out)
                self.assertNotIn("decision", out)

    def test_flags_combine_in_a_fixed_order(self):
        path = self.source(ARRAY)
        out = self.cli(path, "--level", "L3", "--show-vectors", "--show-quads", "--show-plan", "--run")[1]
        marks = [out.index(m) for m in ("Array information", "Quadruples (L3)", "Optimization report", "Run L3")]
        self.assertEqual(marks, sorted(marks))

    def test_flag_order_on_the_command_line_does_not_matter(self):
        path = self.source(ARRAY)
        a = self.cli(path, "--run", "--show-vectors")
        b = self.cli(path, "--show-vectors", "--run")
        self.assertEqual(a, b)

    def test_run_is_added_to_show_flags_only_when_requested(self):
        path = self.source(ARRAY)
        self.assertNotIn("== Run", self.cli(path, "--show-quads")[1])
        self.assertIn("== Run", self.cli(path, "--show-quads", "--run")[1])

    def test_stdout_only_and_nothing_on_stderr(self):
        code, out, err = self.cli(self.source(ARRAY), "--level", "all", "--show-vectors", "--show-quads",
                                  "--show-plan", "--run")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("\n"))

    def test_every_example_runs_at_every_level(self):
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                code, out, err = self.cli(str(path), "--level", "all", "--run")
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out.count("== Run"), 5)


class CompareTests(CliCase):
    def test_compare_requires_level_all(self):
        path = self.source(ARRAY)
        for level in LEVELS:
            with self.subTest(level=level):
                code, out, err = self.cli(path, "--level", level, "--compare")
                self.assertEqual((code, out), (1, ""))
                self.assertIn("--compare requires --level all", err)

    def test_compare_with_default_level_is_rejected(self):
        self.assertEqual(self.cli(self.source(ARRAY), "--compare")[0], 1)

    def test_compare_prints_the_verification_block_with_level_all(self):
        path = self.source(ARRAY)
        code, out, err = self.cli(path, "--level", "all", "--compare")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("== Verification", out)
        self.assertEqual(out.count("== Run"), 5)  # no show flag: the default run still happens
        for level in ("L1", "L2", "hoist", "L3"):
            self.assertIn(f" L0 vs {level}", out)
        self.assertNotIn("not available yet", out)

    def test_compare_block_layout_follows_the_phase_1_spec(self):
        out = self.cli(self.source(ARRAY), "--level", "all", "--compare")[1]
        block = out[out.index("== Verification"):].rstrip("\n").split("\n")
        self.assertEqual(len(block), 1 + 4 + 1)
        for row, level in zip(block[1:5], ("L1", "L2", "hoist", "L3")):
            self.assertRegex(row, rf"^ L0 vs {level} *: scalars equal, memory equal, address trace equal   PASS$")
        self.assertRegex(block[5], r"^ verdict *: PASS$")
        self.assertEqual(len({line.index(":") for line in block[1:]}), 1)  # the colons line up

    def test_compare_pass_exits_zero(self):
        self.assertEqual(self.cli(self.source(ARRAY), "--level", "all", "--compare")[0], 0)

    def test_compare_verdict_comes_last(self):
        path = self.source(ARRAY)
        out = self.cli(path, "--level", "all", "--show-vectors", "--show-quads", "--show-plan", "--run", "--compare")[1]
        self.assertLess(out.index("Run L3"), out.index("== Verification"))
        self.assertEqual(out.count("== Verification"), 1)
        self.assertTrue(out.rstrip().endswith("verdict     : PASS"))

    def test_compare_only_shows_pass_text_for_passing_rows(self):
        out = self.cli(self.source(ARRAY), "--level", "all", "--compare")[1]
        self.assertNotIn("FAIL", out)

    def test_compare_is_checked_before_reading_the_file(self):
        code, _, err = self.cli(str(self.dir / "missing.ml"), "--compare")
        self.assertEqual(code, 1)
        self.assertIn("--compare requires", err)


class ErrorTests(CliCase):
    def assert_error(self, text, code, *needles):
        path = self.source(text)
        got, out, err = self.cli(path, "--level", "all", "--show-quads", "--run")
        self.assertEqual((got, out), (code, ""))  # no partial output
        self.assertEqual(err.count("\n"), 1)  # exactly one message
        for needle in needles:
            self.assertIn(needle.replace("{path}", path), err)

    def test_syntax_error_uses_the_spec_layout(self):
        self.assert_error("s = 1\nt = 2;", 1, "{path}: line 1, col 6: syntax error: expected ';'")

    def test_illegal_character(self):
        self.assert_error("s = 1 @ 2;", 1, "{path}: line 1, col 7: syntax error: illegal character '@'")

    def test_semantic_error(self):
        self.assert_error("s = B[1];", 1, "{path}: line 1: semantic error:", "'B'")

    def test_declaration_error_is_reported(self):
        self.assert_error("array A[5:1] at 0;", 1, "A")

    def test_runtime_error(self):
        self.assert_error("array V[0:9] at 100;\ns = V[10];", 2, "runtime error:")

    def test_missing_file(self):
        code, out, err = self.cli(str(self.dir / "nope.ml"))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("arraypeep: error: cannot read source file", err)

    def test_original_message_text_is_kept(self):
        from arraypeep.lexer import MiniLoopSyntaxError
        from arraypeep.parser import parse

        with self.assertRaises(MiniLoopSyntaxError) as direct:
            parse("s = 1\nt = 2;")
        err = self.cli(self.source("s = 1\nt = 2;"))[2]
        self.assertIn(direct.exception.message, err)

    def test_errors_in_a_later_level_leave_no_output(self):
        path = self.source(SCALAR)
        real = run_file
        calls = []

        def flaky(p, baseline, stage):
            calls.append(stage)
            if stage == "L3":
                from arraypeep.symbol_table import ArrayPeepError
                raise ArrayPeepError("boom")
            return real(p, baseline, stage)

        with mock.patch.object(cli, "run_file", side_effect=flaky):
            code, out, err = self.cli(path, "--level", "all", "--run")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(calls, [None, None, "L2", "hoist", "L3"])
        self.assertIn("boom", err)

    def test_unexpected_programming_errors_are_reported_as_internal_errors(self):
        for error in (RuntimeError("bug"), ValueError("bug"), TypeError("bug"), KeyError("k"), ZeroDivisionError("z")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(cli, "run_file", side_effect=error):
                    code, out, err = self.cli(self.source(SCALAR))
                self.assertEqual((code, out), (4, ""))
                self.assertEqual(err, f"internal error: unexpected {type(error).__name__}: {error}\n")


class SubprocessSmokeTests(CliCase):
    def run_module(self, *argv: str):
        return subprocess.run(
            [sys.executable, "-m", "arraypeep", *argv],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )

    def test_python_dash_m_runs_a_program(self):
        done = self.run_module(self.source(ARRAY), "--level", "L3")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("== Run L3", done.stdout)
        self.assertIn("scalars", done.stdout)

    def test_python_dash_m_all_levels_with_flags(self):
        done = self.run_module(str(EXAMPLES[0]), "--level", "all", "--show-vectors", "--show-quads", "--show-plan",
                               "--run", "--compare")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertIn("verdict     : PASS", done.stdout)

    def test_python_dash_m_exit_codes(self):
        bad = self.run_module(self.source("s = 1 @ 2;"))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("syntax error", bad.stderr)
        self.assertEqual(bad.stdout, "")
        usage = self.run_module(self.source(SCALAR), "--level", "L4")
        self.assertEqual(usage.returncode, 1)
        self.assertIn("usage: arraypeep", usage.stderr)

    def test_python_dash_m_help(self):
        done = self.run_module("--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("--level", done.stdout)


if __name__ == "__main__":
    unittest.main()
