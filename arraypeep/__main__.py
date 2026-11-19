"""Command-line entry point (python -m arraypeep).

    python -m arraypeep FILE [--level {L0,L1,L2,hoist,L3,all}]
                             [--show-vectors] [--show-quads] [--show-plan]
                             [--run] [--compare]

The CLI is a thin layer over ``driver.run_file``: every requested level is
one pipeline run, and all output comes from the existing formatters
(information vectors, quadruple listings, optimization reports, run
results). Levels are run before anything is printed, so an error never
leaves partial output. ``--compare`` (only with ``--level all``) compares
every level with L0 (scalars, memory image, address trace; ``driver.verify``)
and prints the verification block.

Exit codes (docs/language_spec.md Section 9, Phase 10.5): 0 success (and every
requested verification PASS); 1 an unreadable file, a syntax, semantic or
declaration error, or an invalid command line; 2 a run-time error while
executing the program; 3 a verification FAIL; 4 an internal error (a compiler
bug: ill-formed generated code, or any unexpected exception). Every error
prints exactly one line to standard error and never a traceback. Only
``Exception`` is caught: ``KeyboardInterrupt`` and ``SystemExit`` pass through.
"""
import argparse
import sys
from collections.abc import Sequence

from .codegen import CodegenError
from .driver import BASELINE_LEVEL, LevelOutcome, PipelineResult, run_file, verify
from .interpreter import ExecutionError, InternalError, RuntimeAccessError, StepLimitError
from .loop_opt import HoistError, LoopLevelError
from .optimizer import L2BuildError, WindowError
from .report import (
    format_optimization_report,
    format_quadruple_listing,
    format_run,
    format_vectors,
    format_verification,
)
from .symbol_table import ArrayPeepError

PROG = "arraypeep"
DEFAULT_LEVEL = "L3"

LEVEL_RUNS: dict[str, tuple[str, str | None]] = {
    "L0": ("L0", None),
    "L1": ("L1", None),
    "L2": ("L1", "L2"),
    "hoist": ("L1", "hoist"),
    "L3": ("L1", "L3"),
}
"""Selectable level -> (baseline level, optimization stage) passed to ``run_file``."""

ALL_LEVELS = tuple(LEVEL_RUNS)
"""The levels run by ``--level all``, in order."""

LEVEL_CHOICES = (*ALL_LEVELS, "all")
EXIT_OK = 0
EXIT_ERROR = 1  # unreadable file, syntax, semantic or declaration error, invalid command line
EXIT_RUNTIME = 2  # run-time error while executing the program
EXIT_VERIFICATION_FAILED = 3  # a level disagrees with L0
EXIT_INTERNAL = 4  # compiler bug: ill-formed generated code or an unexpected exception

RUNTIME_ERRORS = (RuntimeAccessError, StepLimitError)
INTERNAL_ERRORS = (InternalError, WindowError, L2BuildError, HoistError, LoopLevelError)


class _Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose usage errors exit with ``EXIT_ERROR`` (argparse itself would use 2)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the command line."""
    parser = _Parser(
        prog=PROG,
        description="Array-address peephole optimization of MiniLoop programs.",
    )
    parser.add_argument("file", help="MiniLoop source file (.ml)")
    parser.add_argument(
        "--level",
        choices=LEVEL_CHOICES,
        default=DEFAULT_LEVEL,
        help="code level to generate and run: L0, L1, L2, hoist (hoisting only), L3, "
        "or all (those five in order); default %(default)s",
    )
    parser.add_argument("--show-vectors", action="store_true", help="print the array information vectors")
    parser.add_argument("--show-quads", action="store_true", help="print the quadruple listing of every selected level")
    parser.add_argument("--show-plan", action="store_true", help="print the optimization report of the selected level")
    parser.add_argument("--run", action="store_true",
                        help="print the execution result of every selected level (the default when no show flag is given)")
    parser.add_argument("--compare", action="store_true",
                        help="verify that every level matches L0 in scalars, memory image and address trace "
                        "(requires --level all)")
    return parser


def _plan_text(level: str, results: dict[str, PipelineResult]) -> str:
    """The optimization report available for ``level`` (``all`` shows the L3 one), or an explanation."""
    if level in ("L0", "L1"):
        return f"no optimization plan at level {level}: plans exist for L2, hoist and L3"
    if level in ("L2", "hoist"):
        return format_optimization_report(results[level].l2)
    if "L3" not in results:
        return "no optimization plan: level L3 did not complete"
    return format_optimization_report(results["L3"].optimized.program)


def _error_message(error: ArrayPeepError, path: str) -> str:
    """The one-line message printed for ``error`` (layouts of language_spec.md Section 9)."""
    if hasattr(error, "format"):  # syntax and semantic errors know their own layout
        return error.format(path)
    if str(error).startswith(("runtime error:", "internal error:")):  # these already carry their prefix
        return str(error)
    if error.line is not None:
        return f"{path}: line {error.line}: error: {error.message}"
    return f"{PROG}: error: {error}"


def exit_code(error: ArrayPeepError) -> int:
    """The exit code of a project error: 2 run-time, 4 internal, otherwise 1.

    An execution error that is neither a known run-time error nor an internal
    one is not expected to occur and is treated as internal.
    """
    if isinstance(error, INTERNAL_ERRORS):
        return EXIT_INTERNAL
    if isinstance(error, RUNTIME_ERRORS):
        return EXIT_RUNTIME
    if isinstance(error, ExecutionError):
        return EXIT_INTERNAL
    return EXIT_ERROR


def _attempt(path: str, name: str, tolerate: bool) -> LevelOutcome:
    """Run level ``name``; with ``tolerate`` a run-time error of a non-baseline level becomes an outcome."""
    baseline, stage = LEVEL_RUNS[name]
    try:
        return LevelOutcome(name, run_file(path, baseline, stage))
    except (RuntimeAccessError, StepLimitError) as error:
        if not tolerate or name == BASELINE_LEVEL:
            raise
        return LevelOutcome(name, None, error)


def _execute(args: argparse.Namespace) -> int:
    """Run the requested levels and print the requested blocks; return the exit code (0 or 3).

    Project errors and unexpected exceptions propagate to ``main``.
    """
    levels = ALL_LEVELS if args.level == "all" else (args.level,)
    show_run = args.run or not (args.show_vectors or args.show_quads or args.show_plan)
    outcomes = {name: _attempt(args.file, name, args.compare) for name in levels}
    results = {name: o.pipeline for name, o in outcomes.items() if o.pipeline is not None}
    # a baseline error, or an internal error in any level, propagates from verify()
    verification = verify(list(outcomes.values())) if args.compare else None

    blocks: list[str] = []
    if args.show_vectors:
        blocks.append(format_vectors(results[BASELINE_LEVEL if BASELINE_LEVEL in results else next(iter(results))].symtab.arrays))
    if args.show_quads:
        blocks.extend(format_quadruple_listing(results[name].code.quads, name) for name in levels if name in results)
    if args.show_plan:
        blocks.append(_plan_text(args.level, results))
    if show_run:
        blocks.extend(format_run(name, results[name].run) for name in levels if name in results)
    if verification is not None:
        blocks.append(format_verification(verification))
    print("\n\n".join(blocks))
    return EXIT_OK if verification is None or verification.passed else EXIT_VERIFICATION_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code (0, 1, 2, 3 or 4; see the module docstring).

    Every error prints one line to standard error. Only ``Exception`` is
    caught here, so ``KeyboardInterrupt`` and ``SystemExit`` are not affected.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.compare and args.level != "all":
            parser.error("--compare requires --level all")
    except SystemExit as stop:  # argparse exits itself (0 for --help, 1 for a usage error)
        return stop.code if isinstance(stop.code, int) else EXIT_ERROR
    try:
        return _execute(args)
    except ArrayPeepError as error:
        print(_error_message(error, args.file), file=sys.stderr)
        return exit_code(error)
    except Exception as error:  # a bug: anything that is not a project error
        print(f"internal error: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())