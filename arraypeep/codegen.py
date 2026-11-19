"""Syntax-directed translation to quadruples; L0 and L1 array addressing.

Scalar expressions, scalar assignments and counted loops (Phase 4), and array
reads and writes at level L0 or L1 (Phase 5).

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

Array references (docs/quad_spec.md Sections 6 and 7; ``A[E1, ..., En]``):

* L1 (the paper's PLACE/DIM scheme): ``PLACE`` is the place of ``E1`` and ``DIM``
  is 1; for each further subscript, after the code of ``Ek``,
  ``T := PLACE * LIMIT(A,k)``, ``T' := T + Ek``, ``PLACE := T'``, ``DIM := k``.
  ``DIM`` must end equal to the array's dimension count. ``PLACE`` is then
  ``VARPART`` and the access is ``CONSPART[PLACE]`` with ``CONSPART`` taken from
  the Phase 2 information vector. A one-dimensional access has no arithmetic.
* L0 (naive Eq. 1): every lower bound is subtracted and the base address is
  added at run time; the access is ``0[T]``.
* A read is ``(=[ ], BASE[X], -, T)``; a write is ``([ ]=, place, -, BASE[X])``.
  Every quadruple that evaluates a subscript or an address is tagged ``ADDR``;
  the access quadruple and everything else is ``BODY``. In ``A[..] = E`` the
  right side is translated first (language_spec.md 4.4: the reads happen
  before the write), then the subscripts, then the write.
* Each access records an ``AccessDescriptor`` and each ``FOR`` a ``LoopEntry``.

Temporaries are numbered ``T1, T2, ...`` in order of creation, never reused,
and assigned exactly once. No quadruple is optimised here.
"""
from dataclasses import dataclass

from . import ast_nodes as ast
from .quad import (
    OP_LOAD,
    OP_STORE,
    TAG_ADDR,
    TAG_BODY,
    AccessDescriptor,
    Indexed,
    LoopEntry,
    Operand,
    Quad,
)
from .symbol_table import ArrayInfo, ArrayPeepError, SymbolTable

LEVELS = ("L0", "L1")


class CodegenError(ArrayPeepError):
    """The code generator cannot translate a construct (an undeclared array or a wrong subscript count)."""


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


@dataclass(frozen=True)
class GeneratedCode:
    """Result of a translation: the listing, the access descriptors (in access order) and the loop table."""

    quads: list[Quad]
    accesses: list[AccessDescriptor]
    loops: list[LoopEntry]
    level: str


class CodeGenerator:
    """Translates one program at level ``L0`` or ``L1``; ``quads``, ``accesses`` and ``loops`` hold the result.

    ``symtab`` supplies the array information vectors; when it is omitted the
    table is built from the program's own declarations.
    """

    def __init__(self, symtab: SymbolTable | None = None, level: str = "L1") -> None:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {level!r}")
        self.level = level
        self.symtab = symtab
        self.temps = TempGenerator()
        self.quads: list[Quad] = []
        self.accesses: list[AccessDescriptor] = []
        self.loops: list[LoopEntry] = []
        self._open_loops: list[int] = []  # indexes into self.loops of the loops being translated

    # -- emission ------------------------------------------------------------

    def _emit(
        self, op: str, arg1: Operand = None, arg2: Operand = None, result: Operand = None, tag: str = TAG_BODY
    ) -> None:
        self.quads.append(Quad(op, arg1, arg2, result, tag))

    def _arith(self, op: str, left: Operand, right: Operand, tag: str) -> str:
        """Emit ``T := left op right`` into a fresh temporary and return it."""
        temp = self.temps.new()
        self._emit(op, left, right, temp, tag)
        return temp

    # -- program and statements ---------------------------------------------------

    def generate(self, program: ast.Program) -> list[Quad]:
        """Translate ``program`` and return the quadruple list (declarations produce no code)."""
        if self.symtab is None:
            self.symtab = SymbolTable()
            for item in program.items:
                if isinstance(item, ast.ArrayDecl):
                    self.symtab.declare_array(item.name, item.bounds, item.base, line=item.line)
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
        depth = len(self._open_loops) + 1
        self.loops.append(LoopEntry(loop.var, loop.lo, loop.hi, depth, len(self.quads) + 1, 0))
        slot = len(self.loops) - 1
        self._open_loops.append(slot)
        self._emit("FOR", loop.var, loop.lo, loop.hi)
        for stmt in loop.body:
            self._item(stmt)
        self._emit("ENDFOR", loop.var)
        self._open_loops.pop()
        e = self.loops[slot]
        self.loops[slot] = LoopEntry(e.var, e.lo, e.hi, e.depth, e.for_pos, len(self.quads))

    def _assign(self, stmt: ast.Assign) -> None:
        place = self._expr(stmt.value)
        if isinstance(stmt.target, ast.ArrayRef):
            self._access(stmt.target, place)
        else:
            self._emit(":=", place, None, stmt.target.name)

    # -- expressions --------------------------------------------------------------

    def _expr(self, expr: ast.Expr, tag: str = TAG_BODY) -> Operand:
        """Emit the code of ``expr`` and return its place; its arithmetic gets ``tag``."""
        if isinstance(expr, ast.Number):
            return expr.value
        if isinstance(expr, ast.ScalarRef):
            return expr.name
        if isinstance(expr, ast.BinOp):
            left = self._expr(expr.left, tag)
            right = self._expr(expr.right, tag)
            return self._arith(expr.op, left, right, tag)
        if isinstance(expr, ast.UnaryMinus):
            operand = self._expr(expr.operand, tag)
            return self._arith("-", 0, operand, tag)
        if isinstance(expr, ast.ArrayRef):
            return self._access(expr, None)
        raise TypeError(f"not an expression: {expr!r}")

    # -- array accesses ----------------------------------------------------------------

    def _access(self, ref: ast.ArrayRef, source: Operand) -> Operand:
        """Translate a read (``source`` is None; returns its place) or a write of ``source``."""
        info = self.symtab.get_array(ref.name)
        if info is None:
            raise CodegenError(f"array {ref.name!r} is not declared", ref.line)
        first = len(self.quads) + 1
        if self.level == "L1":
            base, index = self._varpart_l1(ref, info)
        else:
            base, index = self._address_l0(ref, info)
        last = len(self.quads)
        cell = Indexed(base, index)
        if source is None:
            place = self.temps.new()
            self._emit(OP_LOAD, cell, None, place)
        else:
            place = None
            self._emit(OP_STORE, source, None, cell)
        self.accesses.append(
            AccessDescriptor(ref.name, ref.subscripts, first, last, len(self.quads), source is not None)
        )
        return place

    @staticmethod
    def _check_dim(ref: ast.ArrayRef, info: ArrayInfo, dim: int) -> None:
        if dim != info.n:
            raise CodegenError(
                f"array {ref.name!r} has {info.n} dimensions but {dim} subscript(s) given", ref.line
            )

    def _varpart_l1(self, ref: ast.ArrayRef, info: ArrayInfo) -> tuple[int, Operand]:
        """L1: emit ``VARPART``; return ``(CONSPART, PLACE)``."""
        place = self._expr(ref.subscripts[0], TAG_ADDR)
        dim = 1
        for k, sub in enumerate(ref.subscripts[1:], start=2):
            if k > info.n:  # there is no LIMIT(A, k) beyond the last dimension
                self._check_dim(ref, info, len(ref.subscripts))
            value = self._expr(sub, TAG_ADDR)
            product = self._arith("*", place, info.sizes[k - 1], TAG_ADDR)  # LIMIT(A, k)
            place = self._arith("+", product, value, TAG_ADDR)
            dim = k
        self._check_dim(ref, info, dim)
        return info.conspart, place

    def _address_l0(self, ref: ast.ArrayRef, info: ArrayInfo) -> tuple[int, Operand]:
        """L0: emit the full address by Eq. 1; return ``(0, T)``."""
        self._check_dim(ref, info, len(ref.subscripts))
        value = self._expr(ref.subscripts[0], TAG_ADDR)
        acc = self._arith("-", value, info.lowers[0], TAG_ADDR)
        for k in range(1, info.n):
            value = self._expr(ref.subscripts[k], TAG_ADDR)
            scaled = self._arith("*", acc, info.sizes[k], TAG_ADDR)
            offset = self._arith("-", value, info.lowers[k], TAG_ADDR)
            acc = self._arith("+", scaled, offset, TAG_ADDR)
        return 0, self._arith("+", acc, info.base, TAG_ADDR)


def generate(program: ast.Program, symtab: SymbolTable | None = None, level: str = "L1") -> GeneratedCode:
    """Translate ``program`` at ``level``; return the listing, access descriptors and loop table."""
    gen = CodeGenerator(symtab, level)
    gen.generate(program)
    return GeneratedCode(gen.quads, gen.accesses, gen.loops, level)


def generate_code(program: ast.Program, symtab: SymbolTable | None = None, level: str = "L1") -> list[Quad]:
    """Translate ``program`` to a list of quadruples (a fresh generator per call)."""
    return generate(program, symtab, level).quads
