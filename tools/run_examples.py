"""Run every example program at all levels (L0, L1, L2, hoist, L3).

Usage (from the repository root):

    python tools/run_examples.py

Prints one line per example and level, ``<file> <level> PASS`` or
``<file> <level> FAIL: <reason>``, then a totals line. Every run goes through
``arraypeep.driver.run_file`` with the level mapping of the command line
(``arraypeep.__main__.LEVEL_RUNS``); a failing run does not stop the others.
Exit code: 0 when every run passed, 1 when any run failed.
"""
import pathlib
import sys
from collections.abc import Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # `python tools/run_examples.py` puts tools/, not the root, on sys.path
    sys.path.insert(0, str(ROOT))

from arraypeep.__main__ import ALL_LEVELS, LEVEL_RUNS  # noqa: E402
from arraypeep.driver import run_file  # noqa: E402

EXAMPLES_DIR = ROOT / "examples"
LEVELS = ALL_LEVELS


def discover(examples_dir: pathlib.Path = EXAMPLES_DIR) -> list[pathlib.Path]:
    """Every ``.ml`` file directly in ``examples_dir``, sorted by name."""
    return sorted(pathlib.Path(examples_dir).glob("*.ml"))


def run_one(path: pathlib.Path, level: str) -> str | None:
    """Run ``path`` at ``level``; return ``None`` on success, else the failure reason."""
    baseline, stage = LEVEL_RUNS[level]
    try:
        run_file(path, baseline, stage)
    except Exception as error:  # any failure is a result, not a reason to stop
        message = str(error) or "no message"
        return f"{type(error).__name__}: {message}".replace("\n", " ")
    return None


def main(argv: Sequence[str] | None = None, examples_dir: pathlib.Path = EXAMPLES_DIR) -> int:
    """Run all examples at all levels, print the summary, return the exit code (0 or 1)."""
    files = discover(examples_dir)
    total = failed = 0
    for path in files:
        for level in LEVELS:
            reason = run_one(path, level)
            total += 1
            if reason is None:
                print(f"{path.name} {level} PASS")
            else:
                failed += 1
                print(f"{path.name} {level} FAIL: {reason}")
    if not files:
        print(f"no .ml examples found in {examples_dir}")
        return 1
    print(f"{len(files)} examples, {total} runs: {total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
