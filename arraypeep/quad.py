"""Quadruple record with ADDR/BODY tag, access descriptors, loop table and listing printer.

Phase 4 part: the quadruple record, the listing printer and a well-formedness
checker for the opcodes that exist so far (docs/quad_spec.md Sections 1 to 3).
Phase 5 adds the indexed opcodes ``=[ ]`` and ``[ ]=``, the access descriptor
and the loop table.

A quadruple is ``(op, arg1, arg2, result)`` plus a tag, ``ADDR`` for address
computation or ``BODY`` for everything else. Operands are Python values:

* ``int``  a literal,
* ``str``  a scalar, loop variable or temporary (``T1``, ``T2``, ... are
  compiler temporaries, assigned exactly once; ``P1``, ... are L3 address
  temporaries, which may be assigned many times),
* ``None`` an unused field, printed ``-``.

Loop markers are structured: ``FOR v lo hi`` (``arg1`` = variable, ``arg2`` =
lower bound, ``result`` = upper bound) and ``ENDFOR v``.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass

TAG_ADDR = "ADDR"
TAG_BODY = "BODY"
TAGS = (TAG_ADDR, TAG_BODY)

ARITHMETIC_OPS = ("+", "-", "*")
OP_COPY = ":="
OP_FOR = "FOR"
OP_ENDFOR = "ENDFOR"
# Phase 4 opcodes. Phase 5 adds "=[ ]" and "[ ]=" (quad_spec.md Section 3: eight in all).
OPCODES = frozenset(ARITHMETIC_OPS + (OP_COPY, OP_FOR, OP_ENDFOR))

Operand = int | str | None

LISTING_WIDTH = 74
"""Width of the ``== Title ====...`` header line of a report block (quad_spec.md Section 9)."""

_TEMP = re.compile(r"^T[0-9]+$")
_ADDR_TEMP = re.compile(r"^P[0-9]+$")


@dataclass(frozen=True)
class Quad:
    """One quadruple ``(op, arg1, arg2, result)`` with its ``ADDR``/``BODY`` tag."""

    op: str
    arg1: Operand = None
    arg2: Operand = None
    result: Operand = None
    tag: str = TAG_BODY

    def __post_init__(self) -> None:
        if self.op not in OPCODES:
            raise ValueError(f"unknown opcode {self.op!r}")
        if self.tag not in TAGS:
            raise ValueError(f"tag must be one of {TAGS}, got {self.tag!r}")
        for operand in (self.arg1, self.arg2, self.result):
            if isinstance(operand, bool) or not (operand is None or isinstance(operand, (int, str))):
                raise ValueError(f"bad operand {operand!r}")


def is_temp(operand: Operand) -> bool:
    """True for a compiler temporary ``T<n>``."""
    return isinstance(operand, str) and _TEMP.match(operand) is not None


def is_addr_temp(operand: Operand) -> bool:
    """True for an L3 address temporary ``P<n>``."""
    return isinstance(operand, str) and _ADDR_TEMP.match(operand) is not None


def format_operand(operand: Operand) -> str:
    """Printed form of an operand: ``-`` for an unused field."""
    return "-" if operand is None else str(operand)


def format_listing(quads: Sequence[Quad], title: str | None = None) -> str:
    """Numbered listing of ``quads`` (quad_spec.md Section 9.2); no trailing newline.

    Columns: line number (width 3), tag, opcode, ``arg1``, ``arg2``,
    ``result``. Unused fields print ``-``; ``ENDFOR`` prints only its
    variable. Column widths are those of the sample in the specification and
    grow when an operand is wider. With a ``title`` the listing starts with a
    ``== <title> ===...`` header line of ``LISTING_WIDTH`` characters.
    """
    rows = []
    for q in quads:
        cells = [format_operand(q.arg1), format_operand(q.arg2), format_operand(q.result)]
        if q.op == OP_ENDFOR:
            cells = cells[:1]
        rows.append((q, cells))
    w_op = max([8] + [len(q.op) + 1 for q, _ in rows])
    w1 = max([9] + [len(c[0]) + 1 for _, c in rows if len(c) > 1])
    w2 = max([9] + [len(c[1]) + 1 for _, c in rows if len(c) > 2])
    lines = []
    if title is not None:
        lines.append(f"== {title} ".ljust(LISTING_WIDTH, "="))
    for number, (q, cells) in enumerate(rows, start=1):
        text = f"{number:>3}  {q.tag:<4}  {q.op:<{w_op}}"
        if len(cells) == 1:
            text += cells[0]
        else:
            text += cells[0].ljust(w1) + cells[1].ljust(w2) + cells[2]
        lines.append(text.rstrip())
    return "\n".join(lines)


def _is_value(operand: Operand) -> bool:
    return operand is not None


def well_formedness_errors(quads: Sequence[Quad]) -> list[str]:
    """Problems that make a listing ill-formed (quad_spec.md Section 3.2); empty if it is well formed.

    Checks: every operand position holds a permitted kind; every ``FOR`` has a
    matching ``ENDFOR`` with the same variable and proper nesting, and a loop
    variable is not the variable of an enclosing loop; every ``T`` is assigned
    exactly once and before every use; every ``P`` is assigned before its first
    use; a loop variable is read only between its ``FOR`` and ``ENDFOR`` and is
    never an assignment target.
    """
    errors: list[str] = []
    loop_vars = {q.arg1 for q in quads if q.op == OP_FOR}
    open_loops: list[str] = []
    assigned_temps: set[str] = set()
    assigned_addr: set[str] = set()

    def read(operand: Operand, n: int, what: str) -> None:
        if not _is_value(operand):
            errors.append(f"quadruple {n}: {what} is missing")
        elif isinstance(operand, str):
            if is_temp(operand) and operand not in assigned_temps:
                errors.append(f"quadruple {n}: temporary {operand} used before it is assigned")
            elif is_addr_temp(operand) and operand not in assigned_addr:
                errors.append(f"quadruple {n}: address temporary {operand} used before it is assigned")
            elif operand in loop_vars and operand not in open_loops:
                errors.append(f"quadruple {n}: loop variable {operand} used outside its loop")

    def write(operand: Operand, n: int, allow_temp: bool) -> None:
        if not isinstance(operand, str):
            errors.append(f"quadruple {n}: result must be a name")
        elif is_temp(operand):
            if not allow_temp:
                errors.append(f"quadruple {n}: ':=' cannot assign the temporary {operand}")
            elif operand in assigned_temps:
                errors.append(f"quadruple {n}: temporary {operand} is assigned more than once")
            assigned_temps.add(operand)
        elif is_addr_temp(operand):
            assigned_addr.add(operand)
        elif operand in loop_vars:
            errors.append(f"quadruple {n}: loop variable {operand} is assigned")

    for n, q in enumerate(quads, start=1):
        if q.op in ARITHMETIC_OPS:
            read(q.arg1, n, "first operand")
            read(q.arg2, n, "second operand")
            if is_temp(q.result) or is_addr_temp(q.result):
                write(q.result, n, allow_temp=True)
            else:
                errors.append(f"quadruple {n}: result of '{q.op}' must be a temporary")
        elif q.op == OP_COPY:
            read(q.arg1, n, "source")
            if q.arg2 is not None:
                errors.append(f"quadruple {n}: ':=' has no second operand")
            write(q.result, n, allow_temp=False)
        elif q.op == OP_FOR:
            if not isinstance(q.arg1, str) or not isinstance(q.arg2, int) or not isinstance(q.result, int):
                errors.append(f"quadruple {n}: FOR needs a variable and two integer bounds")
                continue
            if q.arg1 in open_loops:
                errors.append(f"quadruple {n}: loop variable {q.arg1} is already used by an enclosing loop")
            open_loops.append(q.arg1)
        elif q.op == OP_ENDFOR:
            if q.arg2 is not None or q.result is not None:
                errors.append(f"quadruple {n}: ENDFOR has only a variable")
            if not open_loops:
                errors.append(f"quadruple {n}: ENDFOR without a matching FOR")
            elif open_loops[-1] != q.arg1:
                errors.append(
                    f"quadruple {n}: ENDFOR {q.arg1} closes the loop of {open_loops[-1]} (bad nesting)"
                )
                open_loops.pop()
            else:
                open_loops.pop()
    for var in open_loops:
        errors.append(f"FOR {var} has no matching ENDFOR")
    return errors
