"""Pipeline orchestration: runs the compiler stages in order (Phase 10.1).

The driver is the only module that knows the whole pipeline:

    read -> lex -> parse -> semantic checks (symbol table) -> baseline code
    (L0 or L1) -> optimization stage (none, L2, hoist or L3) -> interpretation

It calls the existing stage functions and returns every intermediate result
in a ``PipelineResult``; it contains no lexing, parsing, code generation,
optimization or interpretation logic of its own. Errors of the stages are
not caught: they propagate with their own type and message. It prints
nothing (report formatting and the command line belong to later phases).

Phase 10.3 adds the equivalence verification: ``compare_runs`` and ``verify``
compare the final scalars, the memory image and the complete address trace of
every level with the baseline level L0 and return structured results
(``CheckResult``, ``LevelComparison``, ``Verification``) for the report
formatting of Phase 10.4. Counters are deliberately not compared.
"""
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass

from . import ast_nodes as ast
from .codegen import LEVELS as BASELINE_LEVELS
from .codegen import GeneratedCode, generate
from .interpreter import ExecutionError, RunResult, RuntimeAccessError, StepLimitError, execute
from .lexer import Token, tokenize
from .loop_opt import STAGES, StageError, StageResult, optimize_stage
from .optimizer import L2Program, optimize_l2
from .parser import parse_tokens
from .semantic import analyze
from .symbol_table import ArrayPeepError, SymbolTable


class DriverError(ArrayPeepError):
    """The pipeline could not start: unreadable source file or unsupported baseline level."""


@dataclass(frozen=True)
class PipelineResult:
    """Everything the pipeline produced for one program.

    ``baseline`` is the generated baseline listing at ``baseline_level``.
    ``stage`` is the requested optimization stage (one of
    ``loop_opt.STAGES``) or ``None`` for no optimization; then ``l2`` and
    ``optimized`` are ``None``. Otherwise ``l2`` is the Phase 8 program that
    the stage was applied to and ``optimized`` the ``StageResult`` of the
    selected stage. ``code`` is the listing that was interpreted (the
    baseline or the optimized one) and ``run`` the result of interpreting it.
    """

    source_path: str | None
    source: str
    tokens: tuple[Token, ...]
    program: ast.Program
    symtab: SymbolTable
    baseline_level: str
    baseline: GeneratedCode
    stage: str | None
    l2: L2Program | None
    optimized: StageResult | None
    code: GeneratedCode
    run: RunResult


def run_source(
    source: str,
    baseline_level: str = "L1",
    stage: str | None = None,
    *,
    path: str | None = None,
) -> PipelineResult:
    """Run the whole pipeline on MiniLoop ``source`` and return the structured result.

    ``baseline_level`` is ``"L0"`` or ``"L1"``; ``stage`` is ``None`` (no
    optimization) or one of ``loop_opt.STAGES`` (``"L2"``, ``"hoist"``,
    ``"L3"``). ``path`` is only recorded in the result. An unsupported level
    raises ``DriverError`` and an unsupported stage raises ``StageError``,
    both before any work is done; syntax, semantic and run-time errors
    propagate unchanged.
    """
    if baseline_level not in BASELINE_LEVELS:
        raise DriverError(
            f"unsupported baseline level {baseline_level!r}; expected one of: {', '.join(BASELINE_LEVELS)}"
        )
    if stage is not None and stage not in STAGES:
        raise StageError(stage)

    tokens = tokenize(source)
    program = parse_tokens(tokens)
    symtab = analyze(program)
    baseline = generate(program, symtab, baseline_level)

    l2: L2Program | None = None
    optimized: StageResult | None = None
    code = baseline
    if stage is not None:
        l2 = optimize_l2(baseline, symtab)
        optimized = optimize_stage(l2, stage)
        code = optimized.code

    run = execute(code.quads, symtab.arrays, code.level)
    return PipelineResult(
        path, source, tuple(tokens), program, symtab, baseline_level, baseline, stage, l2, optimized, code, run
    )


def run_file(path: str | pathlib.Path, baseline_level: str = "L1", stage: str | None = None) -> PipelineResult:
    """Read the MiniLoop file ``path`` (UTF-8) and run the pipeline on it (see ``run_source``).

    An unreadable file raises ``DriverError`` (the original ``OSError`` is its ``__cause__``).
    """
    try:
        source = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise DriverError(f"cannot read source file {str(path)!r}: {error}") from error
    return run_source(source, baseline_level, stage, path=str(path))


# -- Phase 10.3: equivalence verification ------------------------------------------------------

BASELINE_LEVEL = "L0"
"""The level every other level is compared with."""

CHECK_SCALARS = "scalars"
CHECK_MEMORY = "memory"
CHECK_TRACE = "address trace"
CHECKS = (CHECK_SCALARS, CHECK_MEMORY, CHECK_TRACE)
"""The checks of one comparison, always evaluated in this order."""


@dataclass(frozen=True)
class CheckResult:
    """One check of a comparison: ``name`` is in ``CHECKS``; ``difference`` is the first difference or ``None``."""

    name: str
    passed: bool
    difference: str | None


@dataclass(frozen=True)
class LevelComparison:
    """The comparison of ``level`` with ``baseline``.

    ``checks`` holds the three ``CheckResult``s in order. When ``level`` did
    not complete (a run-time error that the baseline did not have), ``error``
    is that exception and ``checks`` is empty.
    """

    baseline: str
    level: str
    checks: tuple[CheckResult, ...]
    error: ExecutionError | None = None

    @property
    def passed(self) -> bool:
        """True when the level completed and every check passed."""
        return self.error is None and all(check.passed for check in self.checks)

    @property
    def first_difference(self) -> str | None:
        """Why the comparison failed (the run-time error, else the first failing check), or ``None``."""
        if self.error is not None:
            if isinstance(self.error, StepLimitError):
                return f"step limit exceeded where {self.baseline} completed"
            where = "" if self.error.position is None else f" at quadruple {self.error.position}"
            return f"runtime error{where} where {self.baseline} completed"
        for check in self.checks:
            if not check.passed:
                return check.difference
        return None


@dataclass(frozen=True)
class LevelOutcome:
    """What running one level produced: its ``PipelineResult`` or the ``ExecutionError`` that stopped it."""

    level: str
    pipeline: PipelineResult | None = None
    error: ExecutionError | None = None


@dataclass(frozen=True)
class Verification:
    """The comparisons of every non-baseline level with ``baseline``, in the order given."""

    baseline: str
    comparisons: tuple[LevelComparison, ...]

    @property
    def passed(self) -> bool:
        """True when every comparison passed."""
        return all(c.passed for c in self.comparisons)

    @property
    def verdict(self) -> str:
        """``"PASS"`` only if every comparison passed, else ``"FAIL"``."""
        return "PASS" if self.passed else "FAIL"


def _first_scalar_difference(b_name: str, b: dict, l_name: str, other: dict) -> str | None:
    for name in sorted(set(b) | set(other)):
        if name not in other:
            return f"scalar {name} is missing in {l_name}: {b_name} {b[name]}"
        if name not in b:
            return f"scalar {name} is missing in {b_name}: {l_name} {other[name]}"
        if b[name] != other[name]:
            return f"scalar {name} differs: {b_name} {b[name]} vs {l_name} {other[name]}"
    return None


def _first_memory_difference(b_name: str, b: dict, l_name: str, other: dict) -> str | None:
    for address in sorted(set(b) | set(other)):
        if address not in other:
            return f"memory address {address} is missing in {l_name}: {b_name} {b[address]}"
        if address not in b:
            return f"memory address {address} is missing in {b_name}: {l_name} {other[address]}"
        if b[address] != other[address]:
            return f"memory differs at address {address}: {b_name} {b[address]} vs {l_name} {other[address]}"
    return None


def _first_trace_difference(b_name: str, b: list, l_name: str, other: list) -> str | None:
    def show(trace, i):
        return "(end of trace)" if i >= len(trace) else f"({trace[i][0]}, {trace[i][1]})"

    for i in range(max(len(b), len(other))):
        if i >= len(b) or i >= len(other) or tuple(b[i]) != tuple(other[i]):
            return f"address trace differs at access {i + 1}: {b_name} {show(b, i)} vs {l_name} {show(other, i)}"
    return None


def compare_runs(baseline_name: str, baseline_run: RunResult, level_name: str, level_run: RunResult) -> LevelComparison:
    """Compare two completed runs: scalars, then memory image, then the ordered address trace.

    All three checks are always evaluated. The first difference of a check is
    its smallest scalar name, lowest memory address or first access index
    (1-based) that differs, including entries present on one side only.
    Counters are not compared.
    """
    differences = (
        _first_scalar_difference(baseline_name, baseline_run.scalars, level_name, level_run.scalars),
        _first_memory_difference(baseline_name, baseline_run.memory, level_name, level_run.memory),
        _first_trace_difference(baseline_name, baseline_run.trace, level_name, level_run.trace),
    )
    checks = tuple(CheckResult(name, d is None, d) for name, d in zip(CHECKS, differences))
    return LevelComparison(baseline_name, level_name, checks)


def verify(outcomes: Sequence[LevelOutcome], baseline: str = BASELINE_LEVEL) -> Verification:
    """Compare every level of ``outcomes`` with the outcome named ``baseline``.

    The baseline must have completed: an execution error of the baseline is
    raised unchanged, and a missing baseline raises ``DriverError``. A level
    whose outcome is an ``ExecutionError`` becomes a failed comparison; only
    ``RuntimeAccessError`` and ``StepLimitError`` are accepted as such an
    error, any other ``ExecutionError`` (an internal error) is raised.
    """
    by_name = {o.level: o for o in outcomes}
    if baseline not in by_name:
        raise DriverError(f"verification needs the baseline level {baseline!r}; levels given: {', '.join(by_name) or 'none'}")
    base = by_name[baseline]
    if base.error is not None:
        raise base.error
    if base.pipeline is None:
        raise DriverError(f"outcome of baseline level {baseline!r} has neither a result nor an error")
    comparisons = []
    for outcome in outcomes:
        if outcome.level == baseline:
            continue
        if outcome.error is not None:
            if not isinstance(outcome.error, (RuntimeAccessError, StepLimitError)):
                raise outcome.error  # e.g. InternalError: a compiler bug, not a verification result
            comparisons.append(LevelComparison(baseline, outcome.level, (), outcome.error))
        elif outcome.pipeline is None:
            raise DriverError(f"outcome of level {outcome.level!r} has neither a result nor an error")
        else:
            comparisons.append(compare_runs(baseline, base.pipeline.run, outcome.level, outcome.pipeline.run))
    return Verification(baseline, tuple(comparisons))