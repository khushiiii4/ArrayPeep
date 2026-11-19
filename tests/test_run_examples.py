"""Tests for tools/run_examples.py (Phase 10.6: the examples runner)."""
import contextlib
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "run_examples.py"
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))
LEVELS = ("L0", "L1", "L2", "hoist", "L3")

_spec = importlib.util.spec_from_file_location("run_examples", SCRIPT)
run_examples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_examples)

GOOD = "s = 0;\nfor i = 1 to 3 do s = s + i; end\n"
BAD = "s = ;\n"  # syntax error


def run_main(examples_dir):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = run_examples.main([], examples_dir)
    return code, out.getvalue().splitlines()


class TempExamples(unittest.TestCase):
    def make_dir(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in files.items():
            (pathlib.Path(tmp.name) / name).write_text(text, encoding="utf-8")
        return pathlib.Path(tmp.name)


class TestDiscovery(TempExamples):
    def test_finds_only_ml_files_sorted(self):
        d = self.make_dir({"b.ml": GOOD, "a.ml": GOOD, "notes.txt": "x"})
        self.assertEqual([p.name for p in run_examples.discover(d)], ["a.ml", "b.ml"])

    def test_project_examples_are_discovered(self):
        self.assertTrue(EXAMPLES)
        self.assertEqual(run_examples.discover(), EXAMPLES)

    def test_levels(self):
        self.assertEqual(run_examples.LEVELS, LEVELS)


class TestRuns(TempExamples):
    def test_every_level_of_every_example_passes(self):
        for path in EXAMPLES:
            for level in LEVELS:
                with self.subTest(example=path.name, level=level):
                    self.assertIsNone(run_examples.run_one(path, level))

    def test_summary_lines_and_exit_zero(self):
        code, lines = run_main(ROOT / "examples")
        self.assertEqual(code, 0)
        expected = [f"{p.name} {lvl} PASS" for p in EXAMPLES for lvl in LEVELS]
        self.assertEqual(lines[:-1], expected)
        n = len(expected)
        self.assertEqual(lines[-1], f"{len(EXAMPLES)} examples, {n} runs: {n} passed, 0 failed")

    def test_failure_reason_and_nonzero_exit(self):
        d = self.make_dir({"bad.ml": BAD})
        code, lines = run_main(d)
        self.assertEqual(code, 1)
        for level, line in zip(LEVELS, lines):
            self.assertTrue(line.startswith(f"bad.ml {level} FAIL: "), line)
            self.assertGreater(len(line), len(f"bad.ml {level} FAIL: "))
        self.assertEqual(lines[-1], "1 examples, 5 runs: 0 passed, 5 failed")

    def test_failure_does_not_stop_remaining_runs(self):
        d = self.make_dir({"a_bad.ml": BAD, "b_good.ml": GOOD})
        code, lines = run_main(d)
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 11)
        self.assertTrue(all("FAIL" in line for line in lines[:5]))
        self.assertEqual(lines[5:10], [f"b_good.ml {lvl} PASS" for lvl in LEVELS])

    def test_one_failing_level_does_not_stop_other_levels(self):
        d = self.make_dir({"g.ml": GOOD})
        real = run_examples.run_file

        def flaky(path, baseline, stage):
            if stage == "L2":
                raise RuntimeError("boom")
            return real(path, baseline, stage)

        with mock.patch.object(run_examples, "run_file", flaky):
            code, lines = run_main(d)
        self.assertEqual(code, 1)
        self.assertEqual(lines[2], "g.ml L2 FAIL: RuntimeError: boom")
        self.assertEqual([line.split()[2] for line in lines[:5]], ["PASS", "PASS", "FAIL:", "PASS", "PASS"])

    def test_no_examples_is_a_failure(self):
        code, lines = run_main(self.make_dir({}))
        self.assertEqual(code, 1)
        self.assertIn("no .ml examples found", lines[0])


class TestFromRepositoryRoot(unittest.TestCase):
    def test_script_runs_from_root(self):
        done = subprocess.run(
            [sys.executable, "tools/run_examples.py"], cwd=ROOT, capture_output=True, text=True, timeout=300
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(len(lines), len(EXAMPLES) * len(LEVELS) + 1)
        self.assertIn(f"{EXAMPLES[0].name} L0 PASS", lines)
        self.assertIn("0 failed", lines[-1])


if __name__ == "__main__":
    unittest.main()
