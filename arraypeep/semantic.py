"""Semantic checks: declarations, subscript count (DIM check), loop-variable rules.

``analyze(program)`` walks the AST in source order, fills a ``SymbolTable``
(arrays, scalars, loop variables) and raises ``SemanticError`` (with the line
of the offending construct) at the **first** violation. When one construct
breaks several rules, the lowest-numbered rule of docs/language_spec.md
Section 5 is reported (design decision 44).

Rules checked here: S1 undeclared array, S2 wrong subscript count, S3 array
name used without subscripts (also before its declaration), S4 duplicate
array, S5 ``upper < lower``, S6 more than 4 dimensions, S7 overlapping arrays,
S8 assignment to a loop variable, S9 nested reuse of a loop variable, S10 a
name that is both a loop variable and an array or ordinary name, S12 reserved
generated names (``T1``, ``P2``, ...). S4 to S7 are enforced by the symbol
table (Phase 2) and their messages are reused unchanged.

S11 (loop variable used outside its loop) is **subsumed by S10** under
decision 44: a read of a loop-variable name outside its loop also breaks S10,
which has the lower number, so S10 is the rule reported. A loop variable read
*inside* its own loop is fine.
"""
import re

from . import ast_nodes as ast
from .symbol_table import ArrayPeepError, SymbolTable

_RESERVED = re.compile(r"^[TP][0-9]+$")


class SemanticError(ArrayPeepError):
    """A semantic error at a source line (``line``)."""

    def format(self, filename: str) -> str:
        """``<file>: line L: semantic error: <message>`` (language_spec.md Section 9)."""
        return f"{filename}: line {self.line}: semantic error: {self.message}"


def _collect_names(items, arrays: set[str], loops: set[str]) -> None:
    for item in items:
        if isinstance(item, ast.ArrayDecl):
            arrays.add(item.name)
        elif isinstance(item, ast.ForLoop):
            loops.add(item.var)
            _collect_names(item.body, arrays, loops)


class _Analyzer:
    def __init__(self, program: ast.Program) -> None:
        self.program = program
        self.table = SymbolTable()
        self.loop_stack: list[str] = []
        # Names are classified over the whole program first, so that a rule that
        # says "anywhere" (S3, S8, S10) is reported at the first offending use.
        self.array_names: set[str] = set()
        self.loop_names: set[str] = set()
        _collect_names(program.items, self.array_names, self.loop_names)

    @staticmethod
    def _fail(message: str, line: int) -> SemanticError:
        return SemanticError(message, line)

    def run(self) -> SymbolTable:
        """Check the whole program and return its symbol table."""
        for item in self.program.items:
            self._item(item)
        return self.table

    # -- items -----------------------------------------------------------------

    def _item(self, item) -> None:
        if isinstance(item, ast.ArrayDecl):
            self._declaration(item)
        elif isinstance(item, ast.ForLoop):
            self._for_loop(item)
        else:
            self._assign(item)

    def _declaration(self, decl: ast.ArrayDecl) -> None:
        name = decl.name
        try:  # S4, S5, S6, S7 (and S10 when a loop variable of that name came earlier)
            self.table.declare_array(name, decl.bounds, decl.base, line=decl.line)
        except ArrayPeepError as exc:
            raise self._fail(exc.message, decl.line) from exc
        if name in self.loop_names:  # S10 for a loop that comes later in the text
            raise self._fail(f"{name!r} is used as a loop variable and as an ordinary name", decl.line)
        if _RESERVED.match(name):  # S12
            raise self._fail(f"{name!r} is reserved for compiler temporaries", decl.line)

    def _for_loop(self, loop: ast.ForLoop) -> None:
        name = loop.var
        if name in self.loop_stack:  # S9
            raise self._fail(f"loop variable {name!r} is already used by an enclosing loop", loop.line)
        if name in self.array_names:  # S10
            raise self._fail(f"{name!r} is used as a loop variable and as an ordinary name", loop.line)
        if _RESERVED.match(name):  # S12
            raise self._fail(f"{name!r} is reserved for compiler temporaries", loop.line)
        self.table.register_loop_variable(name, line=loop.line)
        self.loop_stack.append(name)
        for stmt in loop.body:
            self._item(stmt)
        self.loop_stack.pop()

    def _assign(self, stmt: ast.Assign) -> None:
        target = stmt.target
        if isinstance(target, ast.ArrayRef):
            self._array_ref(target)
        else:
            name = target.name
            if name in self.array_names:  # S3
                raise self._fail(f"array {name!r} used without subscripts", target.line)
            if name in self.loop_names:  # S8 (lower than S10)
                raise self._fail(f"loop variable {name!r} cannot be assigned", target.line)
            self._register_scalar(name, target.line)
        self._expr(stmt.value)

    # -- expressions -------------------------------------------------------------

    def _expr(self, expr: ast.Expr) -> None:
        if isinstance(expr, ast.Number):
            return
        if isinstance(expr, ast.ScalarRef):
            self._scalar_read(expr)
        elif isinstance(expr, ast.ArrayRef):
            self._array_ref(expr)
        elif isinstance(expr, ast.BinOp):
            self._expr(expr.left)
            self._expr(expr.right)
        elif isinstance(expr, ast.UnaryMinus):
            self._expr(expr.operand)
        else:
            raise TypeError(f"not an expression: {expr!r}")

    def _scalar_read(self, ref: ast.ScalarRef) -> None:
        name = ref.name
        if name in self.array_names:  # S3
            raise self._fail(f"array {name!r} used without subscripts", ref.line)
        if name in self.loop_names:
            if name in self.loop_stack:
                return  # a loop variable read inside its own loop
            # S10 (and S11, which has the higher number): outside its loop
            raise self._fail(f"{name!r} is used as a loop variable and as an ordinary name", ref.line)
        self._register_scalar(name, ref.line)

    def _register_scalar(self, name: str, line: int) -> None:
        if _RESERVED.match(name):  # S12
            raise self._fail(f"{name!r} is reserved for compiler temporaries", line)
        self.table.register_scalar(name, line=line)

    def _array_ref(self, ref: ast.ArrayRef) -> None:
        info = self.table.get_array(ref.name)
        if info is None:  # S1: not declared (yet)
            raise self._fail(f"array {ref.name!r} is not declared", ref.line)
        if len(ref.subscripts) != info.n:  # S2 (the paper's DIM check)
            raise self._fail(
                f"array {ref.name!r} has {info.n} dimensions but "
                f"{len(ref.subscripts)} subscript(s) given",
                ref.line,
            )
        for sub in ref.subscripts:
            self._expr(sub)


def analyze(program: ast.Program) -> SymbolTable:
    """Check ``program`` and return its symbol table; raise ``SemanticError`` on the first violation."""
    return _Analyzer(program).run()
