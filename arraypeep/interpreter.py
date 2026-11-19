"""Reference executor for quadruples: counters, final memory and address trace.

Executes a listing by the rules of docs/quad_spec.md Sections 3, 4 and 8:

* Machine state: scalars (created on first mention with value 0, unbounded
  integers), compiler temporaries ``T``/``P`` and loop variables in the same
  name table, and one flat memory that has a cell exactly at the addresses of
  the declared array regions (Section 4.1, 4.2). Every cell starts as
  ``init(x) = (31*x + 7) mod 1009`` (Section 4.4).
* ``FOR`` and ``ENDFOR`` follow Section 3.1 exactly (zero-trip loops skip to
  the quadruple after the matching ``ENDFOR``); the matching table is computed
  once before execution.
* An indexed access has the effective address ``BASE + value(X)``; an address
  outside every declared array is a run-time error naming the quadruple
  (Section 4.3, 10).
* Every executed quadruple is counted (``metrics.Counters``) and every
  indexed access is appended to the address trace as ``("R"|"W", address)``.

The interpreter knows nothing about the optimiser: it runs any well-formed
listing.
"""
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .metrics import Counters
from .quad import (
    OP_COPY,
    OP_ENDFOR,
    OP_FOR,
    OP_LOAD,
    OP_STORE,
    Indexed,
    Operand,
    Quad,
    format_operand,
    is_addr_temp,
    is_temp,
    well_formedness_errors,
)
from .symbol_table import ArrayInfo, ArrayPeepError

DEFAULT_STEP_LIMIT = 10_000_000
INIT_MULTIPLIER = 31
INIT_OFFSET = 7
INIT_MODULUS = 1009


def init_value(address: int) -> int:
    """Initial content of the cell at ``address`` (quad_spec.md 4.4); a mathematical modulo."""
    return (INIT_MULTIPLIER * address + INIT_OFFSET) % INIT_MODULUS


class ExecutionError(ArrayPeepError):
    """Base class of run-time and internal errors of the executor (quad_spec.md Section 10).

    ``position`` is the 1-based position of the offending quadruple (None for
    a step-limit error) and ``level`` the code level when known.
    """

    def __init__(self, message: str, position: int | None = None, level: str | None = None) -> None:
        super().__init__(message)
        self.position = position
        self.level = level


class RuntimeAccessError(ExecutionError):
    """An indexed access whose effective address is outside every declared array."""


class StepLimitError(ExecutionError):
    """More quadruples executed than the step limit allows."""


class InternalError(ExecutionError):
    """The listing is not well formed (a compiler bug, not a user error)."""


def _describe(q: Quad) -> str:
    return f"[{q.op} {format_operand(q.arg1)} {format_operand(q.arg2)} {format_operand(q.result)}]"


def memory_checksum(memory: dict[int, int]) -> str:
    """Deterministic checksum (16 hex digits) of a memory image, independent of insertion order."""
    digest = hashlib.sha256()
    for address in sorted(memory):
        digest.update(f"{address}:{memory[address]};".encode("ascii"))
    return digest.hexdigest()[:16]


@dataclass
class RunResult:
    """Everything observable about one run (quad_spec.md 4.6) plus the counters."""

    scalars: dict[str, int]
    memory: dict[int, int]
    trace: list[tuple[str, int]]
    counters: Counters
    level: str | None = None

    @property
    def checksum(self) -> str:
        """Checksum of the final memory image."""
        return memory_checksum(self.memory)

    def format(self) -> str:
        """Final scalars, memory checksum and counters as text (roadmap 6.7); no trailing newline."""
        scalars = ", ".join(f"{k} = {v}" for k, v in sorted(self.scalars.items())) or "(none)"
        lines = [
            f"scalars      {scalars}",
            f"memory       {len(self.memory)} cells, checksum {self.checksum}",
            f"trace        {len(self.trace)} accesses",
            self.counters.format(),
        ]
        return "\n".join(lines)


def initial_memory(arrays: Iterable[ArrayInfo]) -> dict[int, int]:
    """Flat memory with one cell per element of every declared region, filled with ``init``."""
    memory: dict[int, int] = {}
    for info in arrays:
        for address in range(info.base, info.base + info.size):
            memory[address] = init_value(address)
    return memory


def match_loops(quads: Sequence[Quad]) -> dict[int, int]:
    """Map the 0-based index of every ``FOR`` to that of its ``ENDFOR`` and back (both directions)."""
    match: dict[int, int] = {}
    stack: list[int] = []
    for index, q in enumerate(quads):
        if q.op == OP_FOR:
            stack.append(index)
        elif q.op == OP_ENDFOR:
            if not stack:
                raise InternalError(f"internal error: quadruple {index + 1}: ENDFOR without a matching FOR", index + 1)
            start = stack.pop()
            match[start] = index
            match[index] = start
    if stack:
        raise InternalError(f"internal error: quadruple {stack[-1] + 1}: FOR without a matching ENDFOR", stack[-1] + 1)
    return match


def scalar_names(quads: Sequence[Quad]) -> set[str]:
    """Names of the source scalars mentioned in a listing (not temporaries, not loop variables)."""
    loop_vars = {q.arg1 for q in quads if q.op == OP_FOR}
    names: set[str] = set()
    for q in quads:
        for operand in (q.arg1, q.arg2, q.result):
            if isinstance(operand, Indexed):
                operand = operand.index
            if isinstance(operand, str) and not is_temp(operand) and not is_addr_temp(operand):
                names.add(operand)
    return names - loop_vars


def execute(
    quads: Sequence[Quad],
    arrays: Iterable[ArrayInfo],
    level: str | None = None,
    step_limit: int = DEFAULT_STEP_LIMIT,
) -> RunResult:
    """Run ``quads`` over the declared ``arrays`` and return scalars, memory, trace and counters.

    Raises ``InternalError`` for an ill-formed listing, ``RuntimeAccessError``
    for an access outside every declared array and ``StepLimitError`` when
    more than ``step_limit`` quadruples would be executed.
    """
    problems = well_formedness_errors(quads)
    if problems:
        raise InternalError(f"internal error: {problems[0]}", level=level)
    match = match_loops(quads)
    memory = initial_memory(arrays)
    env: dict[str, int] = {name: 0 for name in scalar_names(quads)}
    trace: list[tuple[str, int]] = []
    counters = Counters(static=len(quads))
    where = f"level {level}, " if level else ""

    def value(operand: Operand, position: int) -> int:
        if isinstance(operand, int):
            return operand
        try:
            return env[operand]
        except KeyError:
            raise InternalError(f"internal error: quadruple {position}: {operand} is read before it is assigned",
                                position, level) from None

    def address(cell: Indexed, position: int) -> int:
        effective = cell.base + value(cell.index, position)
        if effective not in memory:
            q = quads[position - 1]
            raise RuntimeAccessError(
                f"runtime error: {where}quadruple {position} {_describe(q)}: "
                f"effective address {effective} is outside every declared array",
                position,
                level,
            )
        return effective

    pc = 0
    steps = 0
    while pc < len(quads):
        if steps >= step_limit:
            raise StepLimitError(
                f"runtime error: {where}step limit of {step_limit} executed quadruples exceeded", None, level
            )
        steps += 1
        q = quads[pc]
        position = pc + 1
        counters.record(q.op, q.tag)
        op = q.op
        if op == OP_FOR:
            if q.arg2 <= q.result:
                env[q.arg1] = q.arg2
                pc += 1
            else:  # zero-trip: continue after the matching ENDFOR; the variable is not assigned
                pc = match[pc] + 1
        elif op == OP_ENDFOR:
            start = match[pc]
            if env[q.arg1] < quads[start].result:
                env[q.arg1] += 1
                pc = start + 1
            else:
                pc += 1
        elif op == OP_LOAD:
            effective = address(q.arg1, position)
            trace.append(("R", effective))
            env[q.result] = memory[effective]
            pc += 1
        elif op == OP_STORE:
            effective = address(q.result, position)
            trace.append(("W", effective))
            memory[effective] = value(q.arg1, position)
            pc += 1
        elif op == OP_COPY:
            env[q.result] = value(q.arg1, position)
            pc += 1
        else:  # + - *
            a, b = value(q.arg1, position), value(q.arg2, position)
            env[q.result] = a + b if op == "+" else a - b if op == "-" else a * b
            pc += 1

    scalars = {name: env[name] for name in sorted(scalar_names(quads))}
    return RunResult(scalars, memory, trace, counters, level)
