"""AST node kinds, including an array reference with a list of subscripts.

Nodes are immutable dataclasses. Every node has a source position (``line``,
``col``) that is **not** part of equality, so that two trees compare equal
when they have the same shape, whatever the layout of the source (this is what
the round-trip test of roadmap Phase 3 needs).

The array reference ``ArrayRef(name, subscripts)`` holds the array name
together with the whole *list* of subscript expressions, which is what the
paper's rewritten grammar G[V] (``V -> elist ]``, ``elist -> elist , E | i [ E``)
achieves: the translator sees the array and all its subscripts at once.

This module also holds the AST pretty-printer (``format_program``, which
produces MiniLoop source again) and a debugging dump (``dump_program``).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    """Base of all nodes: 1-based source position, ignored by equality."""

    line: int = field(default=0, compare=False, kw_only=True)
    col: int = field(default=0, compare=False, kw_only=True)


# -- expressions -----------------------------------------------------------


@dataclass(frozen=True)
class Number(Node):
    """Integer literal (never negative; a minus sign is a ``UnaryMinus``)."""

    value: int


@dataclass(frozen=True)
class ScalarRef(Node):
    """An identifier used as a value: a scalar variable or a loop variable."""

    name: str


@dataclass(frozen=True)
class ArrayRef(Node):
    """``name[e1, ..., en]``: an array name and the list of its subscript expressions."""

    name: str
    subscripts: tuple["Expr", ...]


@dataclass(frozen=True)
class BinOp(Node):
    """``left op right`` with ``op`` one of ``+``, ``-``, ``*``."""

    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class UnaryMinus(Node):
    """``-operand``."""

    operand: "Expr"


Expr = Number | ScalarRef | ArrayRef | BinOp | UnaryMinus

# -- statements and program --------------------------------------------------


@dataclass(frozen=True)
class ArrayDecl(Node):
    """``array name[l1:u1, ..., ln:un] at base;`` (all bounds and the base are integer constants)."""

    name: str
    bounds: tuple[tuple[int, int], ...]
    base: int


@dataclass(frozen=True)
class Assign(Node):
    """``target = value;`` where the target is a scalar or an array element."""

    target: ScalarRef | ArrayRef
    value: Expr


@dataclass(frozen=True)
class ForLoop(Node):
    """``for var = lo to hi do body end`` with constant bounds and step +1."""

    var: str
    lo: int
    hi: int
    body: tuple["Stmt", ...]


Stmt = Assign | ForLoop
Item = ArrayDecl | Assign | ForLoop


@dataclass(frozen=True)
class Program(Node):
    """A whole program: declarations and statements in source order."""

    items: tuple[Item, ...]


# -- pretty-printer (back to MiniLoop source) ----------------------------------

_PRECEDENCE = {"+": 1, "-": 1, "*": 2}
_ATOMS = (Number, ScalarRef, ArrayRef, UnaryMinus)


def format_expr(expr: Expr) -> str:
    """MiniLoop source of ``expr`` with the fewest parentheses that keep the tree.

    ``+``, ``-`` and ``*`` are left-associative, so a right operand of equal
    precedence is parenthesised (``a - (b - c)``); unary minus binds tighter
    than ``*``.
    """
    if isinstance(expr, Number):
        return str(expr.value)
    if isinstance(expr, ScalarRef):
        return expr.name
    if isinstance(expr, ArrayRef):
        return f"{expr.name}[{', '.join(format_expr(s) for s in expr.subscripts)}]"
    if isinstance(expr, UnaryMinus):
        inner = format_expr(expr.operand)
        if isinstance(expr.operand, BinOp):
            return f"-({inner})"
        return f"- {inner}" if inner.startswith("-") else f"-{inner}"
    if isinstance(expr, BinOp):
        prec = _PRECEDENCE[expr.op]
        left = format_expr(expr.left)
        right = format_expr(expr.right)
        if isinstance(expr.left, BinOp) and _PRECEDENCE[expr.left.op] < prec:
            left = f"({left})"
        if isinstance(expr.right, BinOp) and _PRECEDENCE[expr.right.op] <= prec:
            right = f"({right})"
        return f"{left} {expr.op} {right}"
    raise TypeError(f"not an expression: {expr!r}")


def _format_item(item: Item, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(item, ArrayDecl):
        dims = ", ".join(f"{lo}:{hi}" for lo, hi in item.bounds)
        return [f"{pad}array {item.name}[{dims}] at {item.base};"]
    if isinstance(item, Assign):
        return [f"{pad}{format_expr(item.target)} = {format_expr(item.value)};"]
    if isinstance(item, ForLoop):
        lines = [f"{pad}for {item.var} = {item.lo} to {item.hi} do"]
        for stmt in item.body:
            lines.extend(_format_item(stmt, depth + 1))
        lines.append(f"{pad}end")
        return lines
    raise TypeError(f"not a statement: {item!r}")


def format_program(program: Program) -> str:
    """MiniLoop source of ``program``, two spaces per nesting level, ending with a newline."""
    lines: list[str] = []
    for item in program.items:
        lines.extend(_format_item(item, 0))
    return "\n".join(lines) + "\n" if lines else ""


# -- debugging dump -----------------------------------------------------------


def _dump_expr(expr: Expr, depth: int, label: str = "") -> list[str]:
    pad = "  " * depth + (f"{label}: " if label else "")
    if isinstance(expr, Number):
        return [f"{pad}Number {expr.value}"]
    if isinstance(expr, ScalarRef):
        return [f"{pad}ScalarRef {expr.name}"]
    if isinstance(expr, ArrayRef):
        lines = [f"{pad}ArrayRef {expr.name} ({len(expr.subscripts)} subscript(s))"]
        for sub in expr.subscripts:
            lines.extend(_dump_expr(sub, depth + 1))
        return lines
    if isinstance(expr, UnaryMinus):
        return [f"{pad}UnaryMinus"] + _dump_expr(expr.operand, depth + 1)
    if isinstance(expr, BinOp):
        return (
            [f"{pad}BinOp {expr.op}"]
            + _dump_expr(expr.left, depth + 1)
            + _dump_expr(expr.right, depth + 1)
        )
    raise TypeError(f"not an expression: {expr!r}")


def _dump_item(item: Item, depth: int) -> list[str]:
    pad = "  " * depth
    if isinstance(item, ArrayDecl):
        dims = ", ".join(f"{lo}:{hi}" for lo, hi in item.bounds)
        return [f"{pad}ArrayDecl {item.name} [{dims}] at {item.base}  (line {item.line})"]
    if isinstance(item, Assign):
        return (
            [f"{pad}Assign  (line {item.line})"]
            + _dump_expr(item.target, depth + 1, "target")
            + _dump_expr(item.value, depth + 1, "value")
        )
    if isinstance(item, ForLoop):
        lines = [f"{pad}ForLoop {item.var} = {item.lo} to {item.hi}  (line {item.line})"]
        for stmt in item.body:
            lines.extend(_dump_item(stmt, depth + 1))
        return lines
    raise TypeError(f"not a statement: {item!r}")


def dump_program(program: Program) -> str:
    """An indented text dump of the tree, for debugging and for reading examples."""
    lines = ["Program"]
    for item in program.items:
        lines.extend(_dump_item(item, 1))
    return "\n".join(lines)
