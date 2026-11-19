"""Three-way equivalence harness (Phase 11.2).

``check_program`` runs one MiniLoop program at L0, L1, L2, hoist and L3
through the public driver and compares:

* final scalars, the final memory image and the ordered address trace of
  every level other than L0 with L0 (``driver.compare_runs``);
* the address trace of every level, L0 included, with the trace produced by
  the independent oracle (``tests/oracle.py``, plain Eq. 1).

A failure names the program, the level, the comparison and the first
difference. This module is a test helper only; it changes no production code.
"""
import pathlib
import sys
from dataclasses import dataclass

TESTS_DIR = pathlib.Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for _path in (str(ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from arraypeep.__main__ import ALL_LEVELS, LEVEL_RUNS  # noqa: E402
from arraypeep.driver import BASELINE_LEVEL, PipelineResult, compare_runs, run_source  # noqa: E402
from arraypeep.interpreter import ExecutionError  # noqa: E402

LEVELS = ALL_LEVELS  # ("L0", "L1", "L2", "hoist", "L3")

RUN = "run"
SCALARS = "scalars"
MEMORY = "memory"
TRACE_L0 = "address trace vs L0"
TRACE_ORACLE = "address trace vs oracle"

_COMPARISON_NAMES = {"scalars": SCALARS, "memory": MEMORY, "address trace": TRACE_L0}


@dataclass(frozen=True)
class Finding:
    """One failed comparison of one level of one program."""

    program: str
    level: str
    comparison: str
    difference: str

    def __str__(self) -> str:
        return f"{self.program}: {self.level}: {self.comparison}: {self.difference}"


def run_levels(source: str, name: str = "<program>") -> tuple[dict[str, PipelineResult], list[Finding]]:
    """Run ``source`` at every level; return the pipeline results and a ``run`` finding for each level that raised.

    Only run-time errors of the executor (``ExecutionError``) are collected;
    syntax and semantic errors propagate, since the program itself is wrong.
    """
    results: dict[str, PipelineResult] = {}
    findings: list[Finding] = []
    for level in LEVELS:
        baseline, stage = LEVEL_RUNS[level]
        try:
            results[level] = run_source(source, baseline, stage, path=name)
        except ExecutionError as error:
            findings.append(Finding(name, level, RUN, f"{type(error).__name__}: {error}"))
    return results, findings


def trace_difference(expected_name: str, expected, actual_name: str, actual) -> str | None:
    """The first difference of two ordered traces (1-based access index), or ``None`` when they are equal."""

    def show(trace, i):
        return "(end of trace)" if i >= len(trace) else f"({trace[i][0]}, {trace[i][1]})"

    for i in range(max(len(expected), len(actual))):
        if i >= len(expected) or i >= len(actual) or tuple(expected[i]) != tuple(actual[i]):
            return (f"differs at access {i + 1}: {expected_name} {show(expected, i)} vs {actual_name} {show(actual, i)}"
                    f" ({expected_name} has {len(expected)} accesses, {actual_name} {len(actual)})")
    return None


def find_differences(name: str, runs: dict, oracle_trace) -> list[Finding]:
    """Every failed comparison, for ``runs`` (level -> ``RunResult``) against L0 and against ``oracle_trace``.

    Levels missing from ``runs`` (they raised) are skipped here; ``run_levels``
    reports them. Without L0 no comparison with L0 is possible.
    """
    findings: list[Finding] = []
    if BASELINE_LEVEL in runs:
        for level in LEVELS:
            if level == BASELINE_LEVEL or level not in runs:
                continue
            comparison = compare_runs(BASELINE_LEVEL, runs[BASELINE_LEVEL], level, runs[level])
            for check in comparison.checks:
                if not check.passed:
                    findings.append(Finding(name, level, _COMPARISON_NAMES[check.name], check.difference))
    for level in LEVELS:
        if level in runs:
            difference = trace_difference("oracle", oracle_trace, level, runs[level].trace)
            if difference is not None:
                findings.append(Finding(name, level, TRACE_ORACLE, difference))
    return findings


def check_program(source: str, oracle_trace, name: str = "<program>") -> tuple[dict[str, PipelineResult], list[Finding]]:
    """Run ``source`` at all five levels and return ``(pipeline results, findings)``; no findings means equivalent."""
    results, findings = run_levels(source, name)
    findings += find_differences(name, {level: r.run for level, r in results.items()}, oracle_trace)
    return results, findings


def assert_equivalent(testcase, source: str, oracle_trace, name: str = "<program>") -> dict[str, PipelineResult]:
    """``check_program`` that fails ``testcase`` with one line per finding; returns the pipeline results."""
    results, findings = check_program(source, oracle_trace, name)
    if findings:
        testcase.fail(f"{len(findings)} equivalence failure(s):\n" + "\n".join(f"  {f}" for f in findings))
    return results


def read_example(filename: str) -> str:
    """The text of ``examples/<filename>``."""
    return (ROOT / "examples" / filename).read_text(encoding="utf-8")
