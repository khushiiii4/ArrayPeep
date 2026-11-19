"""Symbol table holding array information vectors (paper Table 1), scalars and loop variables.

This part of the module defines

* the error base class ``ArrayPeepError`` (design decision 50; it may move to
  its own module in Phase 10) and ``DeclarationError`` for invalid array
  declarations, and
* ``ArrayInfo``, the immutable *array information vector* of the paper's
  Table 1: bounds ``l_i``, ``u_i``, sizes ``d_i``, dimension count ``n``,
  element type, constant ``C`` and base address ``a``, plus the derived
  ``CONSPART``, strides and size, and
* ``SymbolTable``, which registers arrays, scalars and loop variables and
  enforces the rules that need to see all names at once: S3 (an array name
  is never a scalar), S4 (no duplicate array), S7 (no overlapping arrays) and
  S10 (a loop-variable name is not also an array or scalar name). The scoping
  rules S8, S9, S11 and S12 need the AST and belong to ``semantic.py``
  (design decision 51), and
* the printer ``format_information_vectors`` / ``format_array_info`` that
  renders array information vectors in the layout of quad_spec.md Section 9.1
  (it returns a string; printing is left to the caller).

All arithmetic is done by ``address_math``; this module only validates the
declaration and stores the results. It knows nothing about the lexer, the
parser or the AST, so it can be tested alone (roadmap Phase 2).
"""
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Callable, TypeVar

from . import address_math as am

MAX_DIMENSIONS = 4
"""Largest number of dimensions of an array (language_spec.md, rule S6)."""

_T = TypeVar("_T")


class ArrayPeepError(Exception):
    """Base class of every error raised by ArrayPeep (design decision 50).

    ``line`` is the optional source line of the offending construct; the
    front end (Phase 3) attaches it when it knows it. ``str(error)`` is the
    message without any position.
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line


class DeclarationError(ArrayPeepError):
    """An array declaration violates a rule of language_spec.md (S4 to S7, static bounds)."""


class DuplicateArrayError(DeclarationError):
    """Rule S4: an array name is declared twice."""

    def __init__(self, name: str, line: int | None = None) -> None:
        super().__init__(f"array {name!r} is already declared", line)
        self.name = name


class OverlapError(DeclarationError):
    """Rule S7: the new array shares an address with an already declared array."""

    def __init__(self, new: "ArrayInfo", existing: "ArrayInfo", line: int | None = None) -> None:
        super().__init__(
            f"array {new.name!r} (addresses {new.base}..{new.last_address}) "
            f"overlaps array {existing.name!r} "
            f"(addresses {existing.base}..{existing.last_address})",
            line,
        )
        self.name = new.name
        self.other = existing.name


class NameConflictError(ArrayPeepError):
    """Rule S3 or S10: one name is used for two different kinds of thing."""

    def __init__(self, message: str, name: str, line: int | None = None) -> None:
        super().__init__(message, line)
        self.name = name


def _translate(name: str, dimension: int | None, action: Callable[[], _T]) -> _T:
    """Run ``action``; turn ``address_math`` errors into ``DeclarationError``.

    The message of S5 reads ``upper bound 4 is less than lower bound 5 in
    dimension 1 of array 'B'``; other messages are prefixed with the array
    (and dimension) they refer to.
    """
    try:
        return action()
    except ValueError as exc:
        where = f" in dimension {dimension} of array {name!r}" if dimension else f" in array {name!r}"
        raise DeclarationError(f"{exc}{where}") from exc
    except TypeError as exc:
        where = f"array {name!r}, dimension {dimension}" if dimension else f"array {name!r}"
        raise DeclarationError(f"{where}: {exc}") from exc


@dataclass(frozen=True, init=False, slots=True)
class ArrayInfo:
    """Array information vector (paper Table 1) of one static array.

    Build it from the declaration ``array name[l1:u1, ..., ln:un] at base;``::

        info = ArrayInfo("A", [(1, 10), (1, 20)], 1000)

    Fields (all immutable; sequences are tuples):

    ``name``          array name
    ``n``             number of dimensions (1 to 4)
    ``lowers``        lower bounds ``(l1, ..., ln)``
    ``uppers``        upper bounds ``(u1, ..., un)``
    ``sizes``         dimension sizes ``(d1, ..., dn)``, ``d_k = u_k - l_k + 1``
    ``c``             constant ``C`` of the information vector (paper Eq. 2)
    ``base``          base address ``a``, the address of ``A[l1, ..., ln]``
    ``conspart``      ``a - C``, the address of the notional ``A[0, ..., 0]``
    ``strides``       row-major strides ``(stride_1, ..., stride_n)``
    ``size``          number of elements ``d1 * ... * dn``
    ``element_type``  always ``"int"``

    Raises ``DeclarationError`` for: an empty name; no dimensions; more than
    ``MAX_DIMENSIONS`` dimensions (S6); a bound or base address that is not an
    integer constant, ``bool`` included (variable-bound arrays are not
    supported); an upper bound smaller than its lower bound (S5). If a
    declaration breaks both S5 and S6, S5 is reported (design decision 44: the
    lowest-numbered rule wins). Duplicate names and overlapping regions are
    checks of the symbol table, not of one record.

    Note: ``dataclasses.replace()`` is not supported. ``ArrayInfo`` has a
    custom constructor taking ``(name, bounds, base_address)`` instead of one
    parameter per field, so ``replace()`` would call it with the field names
    (``n``, ``lowers``, ...) and raise ``TypeError``. Build a new record with
    ``ArrayInfo(name, bounds, base_address)`` instead.
    """

    name: str
    n: int
    lowers: tuple[int, ...]
    uppers: tuple[int, ...]
    sizes: tuple[int, ...]
    c: int
    base: int
    conspart: int
    strides: tuple[int, ...]
    size: int
    element_type: str

    def __init__(
        self,
        name: str,
        bounds: Sequence[tuple[int, int]],
        base_address: int,
    ) -> None:
        if not isinstance(name, str) or name == "":
            raise DeclarationError(f"array name must be a non-empty string, got {name!r}")
        if isinstance(bounds, (str, bytes)) or not isinstance(bounds, Sequence):
            raise DeclarationError(
                f"array {name!r}: bounds must be a sequence of (lower, upper) pairs, got {bounds!r}"
            )
        n = len(bounds)
        if n == 0:
            raise DeclarationError(f"array {name!r} has no dimensions; at least 1 is required")
        if n > MAX_DIMENSIONS:
            # Design decision 44: the lowest-numbered rule wins and S5 < S6, so an
            # invalid bound (upper < lower) is reported before the dimension count.
            # Only well-formed integer pairs are scanned; every other check keeps its order.
            for k, pair in enumerate(bounds, start=1):
                if (
                    isinstance(pair, Sequence)
                    and not isinstance(pair, (str, bytes))
                    and len(pair) == 2
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in pair)
                    and pair[1] < pair[0]
                ):
                    _translate(name, k, lambda lo=pair[0], up=pair[1]: am.dim_size(lo, up))
            raise DeclarationError(
                f"array {name!r} has {n} dimensions; at most {MAX_DIMENSIONS} are supported"
            )

        lowers: list[int] = []
        uppers: list[int] = []
        sizes: list[int] = []
        for k, pair in enumerate(bounds, start=1):
            if isinstance(pair, (str, bytes)) or not isinstance(pair, Sequence) or len(pair) != 2:
                raise DeclarationError(
                    f"array {name!r}, dimension {k}: expected a (lower, upper) pair, got {pair!r}"
                )
            lower, upper = pair
            sizes.append(_translate(name, k, lambda lo=lower, up=upper: am.dim_size(lo, up)))
            lowers.append(lower)
            uppers.append(upper)

        c = _translate(name, None, lambda: am.info_constant_c(lowers, sizes))
        conspart = _translate(name, None, lambda: am.conspart(base_address, c))

        # Frozen dataclass: set the fields through object.__setattr__.
        values = {
            "name": name,
            "n": n,
            "lowers": tuple(lowers),
            "uppers": tuple(uppers),
            "sizes": tuple(sizes),
            "c": c,
            "base": base_address,
            "conspart": conspart,
            "strides": am.strides(sizes),
            "size": am.array_size(sizes),
            "element_type": "int",
        }
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)

    @property
    def last_address(self) -> int:
        """Address of the last element ``A[u1, ..., un]``: ``a + size - 1``.

        The array occupies the region ``base .. last_address``.
        """
        return self.base + self.size - 1


KIND_ARRAY = "array"
KIND_SCALAR = "scalar"
KIND_LOOP_VARIABLE = "loop variable"


def _regions_overlap(a: ArrayInfo, b: ArrayInfo) -> bool:
    """True if the inclusive address ranges ``base..last_address`` share an address.

    Adjacent regions (``a.last_address + 1 == b.base``) do not overlap.
    """
    return a.base <= b.last_address and b.base <= a.last_address


def _check_variable_name(name: object, line: int | None) -> None:
    """A variable name must be a non-empty string (identifier syntax is the lexer's job)."""
    if not isinstance(name, str) or name == "":
        raise DeclarationError(f"name must be a non-empty string, got {name!r}", line)


def _array_without_subscripts(name: str, line: int | None) -> NameConflictError:
    """Rule S3 error: an array name used as a scalar."""
    return NameConflictError(f"array {name!r} used without subscripts", name, line)


def _loop_and_ordinary(name: str, line: int | None) -> NameConflictError:
    """Rule S10 error: a loop-variable name that is also an array or scalar name."""
    return NameConflictError(
        f"{name!r} is used as a loop variable and as an ordinary name", name, line
    )


REPORT_WIDTH = 74
"""Total width of the ``== Title ====...`` header line of every report block (quad_spec.md 9)."""

_VECTOR_TITLE = "Array information vector"
_NO_ARRAYS_LINE = " (no arrays declared)"


def _block_header(title: str) -> str:
    """``== <title> ===...`` padded with ``=`` to ``REPORT_WIDTH`` characters."""
    return f"== {title} ".ljust(REPORT_WIDTH, "=")


def format_array_info(info: ArrayInfo) -> str:
    """One information-vector block for ``info`` (quad_spec.md 9.1, paper Table 1).

    For ``A[1:10, 1:20] at 1000`` the result is (no trailing newline)::

        == Array information vector ==============================================
         array A   n = 2   type = int   a = 1000
          dim  l   u   d
           1   1   10  10     stride 20
           2   1   20  20     stride 1
         C = 21    CONSPART = a - C = 979    size = 200

    The ``l``, ``u`` and ``d`` columns are left-aligned; their widths are the
    ones of the sample (4, 4, 7) and grow when a value is wider, so the
    columns of one block always line up. Negative numbers print with ``-``.
    """
    lowers = [str(v) for v in info.lowers]
    uppers = [str(v) for v in info.uppers]
    sizes = [str(v) for v in info.sizes]
    w_l = max(4, max(map(len, lowers)) + 1)
    w_u = max(4, max(map(len, uppers)) + 1)
    w_d = max(7, max(map(len, sizes)) + 2)
    lines = [
        _block_header(_VECTOR_TITLE),
        f" array {info.name}   n = {info.n}   type = {info.element_type}   a = {info.base}",
        f"  dim  {'l':<{w_l}}{'u':<{w_u}}d",
    ]
    for k in range(info.n):
        lines.append(
            f"{k + 1:>4}   {lowers[k]:<{w_l}}{uppers[k]:<{w_u}}{sizes[k]:<{w_d}}"
            f"stride {info.strides[k]}"
        )
    lines.append(
        f" C = {info.c}    CONSPART = a - C = {info.conspart}    size = {info.size}"
    )
    return "\n".join(lines)


def format_information_vectors(arrays: Iterable[ArrayInfo]) -> str:
    """Information vectors of ``arrays``, one block each, in the order given.

    Blocks are separated by one blank line; there is no trailing newline. For
    no arrays at all the result is the block header followed by the line
    `` (no arrays declared)``. The output depends only on the records, so it
    is deterministic.
    """
    blocks = [format_array_info(info) for info in arrays]
    if not blocks:
        return f"{_block_header(_VECTOR_TITLE)}\n{_NO_ARRAYS_LINE}"
    return "\n\n".join(blocks)


class SymbolTable:
    """Registry of arrays, scalar variables and loop variables.

    Every name has exactly one kind (``KIND_ARRAY``, ``KIND_SCALAR`` or
    ``KIND_LOOP_VARIABLE``). Registration methods raise a subclass of
    ``ArrayPeepError`` on a violation and then leave the table unchanged;
    each takes an optional source ``line`` that is attached to the error.
    When one registration breaks several rules, the lowest-numbered rule is
    reported (design decision 44): S3, S4, S5, S6, S7, S10 for an array;
    S3, S10 for a scalar.

    Not checked here (they need the AST, Phase 3): S8 assignment to a loop
    variable, S9 nested reuse of a loop variable, S11 use outside its loop,
    S12 reserved generated names. Registering a loop variable again is
    therefore allowed (sequential loops may reuse a name).
    """

    def __init__(self) -> None:
        self._arrays: dict[str, ArrayInfo] = {}
        self._scalars: dict[str, None] = {}  # dict used as an ordered set
        self._loop_variables: dict[str, None] = {}

    # -- registration -------------------------------------------------------

    def declare_array(
        self,
        name: str,
        bounds: Sequence[tuple[int, int]],
        base_address: int,
        line: int | None = None,
    ) -> ArrayInfo:
        """Declare ``array name[l1:u1, ...] at base_address`` and return its record.

        Errors, in this order: ``NameConflictError`` (S3, the name is a
        scalar); ``DuplicateArrayError`` (S4); ``DeclarationError`` from
        ``ArrayInfo`` (S5, S6, non-integer bounds or base);
        ``OverlapError`` (S7, the first overlapping array in declaration order
        is named); ``NameConflictError`` (S10, the name is a loop variable).
        """
        _check_variable_name(name, line)
        if name in self._scalars:
            raise _array_without_subscripts(name, line)
        if name in self._arrays:
            raise DuplicateArrayError(name, line)
        try:
            info = ArrayInfo(name, bounds, base_address)
        except ArrayPeepError as exc:
            if exc.line is None:
                exc.line = line
            raise
        for existing in self._arrays.values():
            if _regions_overlap(info, existing):
                raise OverlapError(info, existing, line)
        if name in self._loop_variables:
            raise _loop_and_ordinary(name, line)
        self._arrays[name] = info
        return info

    def register_scalar(self, name: str, line: int | None = None) -> None:
        """Register a scalar variable; registering it again is allowed.

        Errors: ``NameConflictError`` S3 if ``name`` is an array, S10 if it
        is a loop variable.
        """
        _check_variable_name(name, line)
        if name in self._arrays:
            raise _array_without_subscripts(name, line)
        if name in self._loop_variables:
            raise _loop_and_ordinary(name, line)
        self._scalars[name] = None

    def register_loop_variable(self, name: str, line: int | None = None) -> None:
        """Register a loop variable; registering it again is allowed.

        Error: ``NameConflictError`` S10 if ``name`` is an array or a scalar.
        """
        _check_variable_name(name, line)
        if name in self._arrays or name in self._scalars:
            raise _loop_and_ordinary(name, line)
        self._loop_variables[name] = None

    # -- lookup -------------------------------------------------------------

    def get_array(self, name: str) -> ArrayInfo | None:
        """The record of the array ``name``, or ``None`` if it is not an array."""
        return self._arrays.get(name)

    def is_array(self, name: str) -> bool:
        """True if ``name`` is a declared array."""
        return name in self._arrays

    def is_scalar(self, name: str) -> bool:
        """True if ``name`` is a registered scalar."""
        return name in self._scalars

    def is_loop_variable(self, name: str) -> bool:
        """True if ``name`` is a registered loop variable."""
        return name in self._loop_variables

    def kind_of(self, name: str) -> str | None:
        """``KIND_ARRAY``, ``KIND_SCALAR``, ``KIND_LOOP_VARIABLE`` or ``None``."""
        if name in self._arrays:
            return KIND_ARRAY
        if name in self._scalars:
            return KIND_SCALAR
        if name in self._loop_variables:
            return KIND_LOOP_VARIABLE
        return None

    def __contains__(self, name: object) -> bool:
        return name in self._arrays or name in self._scalars or name in self._loop_variables

    @property
    def arrays(self) -> tuple[ArrayInfo, ...]:
        """All array records, in declaration order."""
        return tuple(self._arrays.values())

    @property
    def scalars(self) -> tuple[str, ...]:
        """All scalar names, in order of first registration."""
        return tuple(self._scalars)

    @property
    def loop_variables(self) -> tuple[str, ...]:
        """All loop-variable names, in order of first registration."""
        return tuple(self._loop_variables)

    # -- printing -----------------------------------------------------------

    def format_information_vectors(self) -> str:
        """Information vectors of all arrays in declaration order (see the module function)."""
        return format_information_vectors(self.arrays)
