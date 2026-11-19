"""Syntax-directed translation to quadruples; L0 and L1 array addressing.

Phase 4 part (this file): scalar expressions, scalar assignments and counted
loops. Array references are a clearly marked stub that raises
``CodegenError`` ("not yet implemented"); Phase 5 replaces it with the L0 and
L1 address code.

Translation (docs/quad_spec.md Section 3 and roadmap Phase 4):

* An expression has a *place*: a number or a scalar yields itself (an ``int``
  or the variable name); ``left op right`` emits one quadruple
  ``(op, place(left), place(right), T)`` into a fresh temporary and yields it.
  Operands are translated left to right (language_spec.md 4.4).
* Unary minus has no opcode of its own: ``-E`` is ``(-, 0, place(E), T)``.
* A scalar assignment ``x = E;`` is the copy ``(:=, place(E), -, x)``. It always
  goes through the translation of ``E`` and a copy; there is no shortcut.
* ``for v = lo to hi do body end`` becomes ``FOR v lo hi``, the translated body,
  and ``ENDFOR v``.
* No constant folding and no other optimisation (roadmap 4.7): the baseline
  stays plain so that measurements are meaningful.

Every quadruple of this phase is tagged ``BODY``. Temporaries are numbered
``T1, T2, ...`` in order of creation, never reused, and assigned exactly once.
"""
from . import ast_nodes as ast
from .quad import TAG_BODY, Operand, Quad
from .symbol_table import ArrayPeepError


class CodegenError(ArrayPeepError):
    """The code generator cannot translate a construct (for now: array references)."""


ARRAY_STUB_MESSAGE = "array references are not yet implemented (Phase 5)"


class TempGenerator:
    """Generator of fresh temporary names ``T1, T2, ...`` (the paper's "new temporary" procedure)."""

    def __init__(self) -> None:
        self._count = 0

    def new(self) -> str:
        """The next temporary name."""
        self._count += 1
        return f"T{self._count}"

    @property
    def count(self) -> int:
        """How many temporaries have been created."""
        return self._count


class CodeGenerator:
    """Translates one program; ``quads`` holds the listing after ``generate``."""

    def __init__(self) -> None:
        self.temps = TempGenerator()
        self.quads: list[Quad] = []

    # -- emission ------------------------------------------------------------

    def _emit(self, op: str, arg1: Operand = None, arg2: Operand = None, result: Operand = None) -> None:
        self.quads.append(Quad(op, arg1, arg2, result, TAG_BODY))

    # -- program and statements ---------------------------------------------------

    def generate(self, program: ast.Program) -> list[Quad]:
        """Translate ``program`` and return the quadruple list (declarations produce no code)."""
        for item in program.items:
            self._item(item)
        return self.quads

    def _item(self, item) -> None:
        if isinstance(item, ast.ArrayDecl):
            return  # handled by the symbol table; no run-time code
        if isinstance(item, ast.ForLoop):
            self._for_loop(item)
        elif isinstance(item, ast.Assign):
            self._assign(item)
        else:
            raise TypeError(f"not a statement: {item!r}")

    def _for_loop(self, loop: ast.ForLoop) -> None:
        self._emit("FOR", loop.var, loop.lo, loop.hi)
        for stmt in loop.body:
            self._item(stmt)
        self._emit("ENDFOR", loop.var)

    def _assign(self, stmt: ast.Assign) -> None:
        if isinstance(stmt.target, ast.ArrayRef):
            raise self._array_stub(stmt.target)
        place = self._expr(stmt.value)
        self._emit(":=", place, None, stmt.target.name)

    # -- expressions --------------------------------------------------------------

    def _expr(self, expr: ast.Expr) -> Operand:
        """Emit the code of ``expr`` and return its place."""
        if isinstance(expr, ast.Number):
            return expr.value
        if isinstance(expr, ast.ScalarRef):
            return expr.name
        if isinstance(expr, ast.BinOp):
            left = self._expr(expr.left)
            right = self._expr(expr.right)
            temp = self.temps.new()
            self._emit(expr.op, left, right, temp)
            return temp
        if isinstance(expr, ast.UnaryMinus):
            operand = self._expr(expr.operand)
            temp = self.temps.new()
            self._emit("-", 0, operand, temp)
            return temp
        if isinstance(expr, ast.ArrayRef):
            raise self._array_stub(expr)
        raise TypeError(f"not an expression: {expr!r}")

    @staticmethod
    def _array_stub(ref: ast.ArrayRef) -> CodegenError:
        """Stub for array references; Phase 5 replaces it."""
        return CodegenError(f"{ARRAY_STUB_MESSAGE}: {ref.name}[...]", ref.line)


def generate_code(program: ast.Program) -> list[Quad]:
    """Translate ``program`` to a list of quadruples (a fresh generator per call)."""
    return CodeGenerator().generate(program)
