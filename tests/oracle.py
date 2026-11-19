"""Independent oracle for the address trace of a MiniLoop program (Phase 11.1).

This module imports nothing from the ``arraypeep`` package, and it shares no
code or data structures with the compiler. It re-implements, from the
formulas alone:

* the row-major address of an array element, **Eq. 1** of the paper:

      D = a + SUM_k (i_k - l_k) * stride_k,      stride_k = d_(k+1) * ... * d_n,
      d_k = u_k - l_k + 1,                       stride_n = 1

  evaluated directly (no CONSPART / VARPART split, no precomputed constants);
* the initial memory pattern ``init(x) = (31 * x + 7) mod 1009``
  (docs/quad_spec.md 4.4);
* the ordered ``(kind, address)`` trace of a loop nest, where ``kind`` is
  ``"R"`` or ``"W"``.

A test program is described with plain Python data:

    arrays = [Array("A", lowers=(1, 1), uppers=(10, 20), base=1000)]
    body = [
        Loop("i", 1, 4, [
            Loop("j", 2, 20, [
                Statement([read("A", linear(1, i=2), linear(-1, j=1))]),
            ]),
        ]),
    ]
    trace = expected_trace(arrays, body)

``Loop`` runs ``var`` from ``lo`` to ``hi`` inclusive in steps of 1 (zero
times when ``lo > hi``). A ``Statement`` lists its array accesses in the order
in which they happen; for ``A[...] = E;`` that is the reads of ``E`` from left
to right, then the reads inside the target's subscripts, then the write (use
``assign``). A subscript is an ``int`` constant, the name of a loop variable
(``"i"``), or a function that takes the dict of the loop variables currently
in scope and returns an ``int`` (``linear`` builds one from coefficients).
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

READ = "R"
WRITE = "W"

INIT_MULTIPLIER = 31
INIT_OFFSET = 7
INIT_MODULUS = 1009

Subscript = int | str | Callable[[Mapping[str, int]], int]


class OracleError(Exception):
    """An ill-formed test description (unknown array or variable, subscript out of bounds)."""


# -- arrays and Eq. 1 --------------------------------------------------------------------------


@dataclass(frozen=True)
class Array:
    """``name[lowers[0]:uppers[0], ...]`` stored row-major from address ``base`` (one cell per element)."""

    name: str
    lowers: tuple[int, ...]
    uppers: tuple[int, ...]
    base: int

    def __post_init__(self):
        if len(self.lowers) != len(self.uppers) or not self.lowers:
            raise OracleError(f"array {self.name}: lowers and uppers must have the same non-zero length")
        if any(u < l for l, u in zip(self.lowers, self.uppers)):
            raise OracleError(f"array {self.name}: an upper bound is below its lower bound")

    @property
    def sizes(self) -> tuple[int, ...]:
        """``d_k = u_k - l_k + 1`` for every dimension."""
        return tuple(u - l + 1 for l, u in zip(self.lowers, self.uppers))

    @property
    def strides(self) -> tuple[int, ...]:
        """``stride_k = d_(k+1) * ... * d_n`` (the last stride is 1)."""
        sizes = self.sizes
        strides = []
        for k in range(len(sizes)):
            stride = 1
            for d in sizes[k + 1:]:
                stride *= d
            strides.append(stride)
        return tuple(strides)

    @property
    def size(self) -> int:
        """Number of elements, ``d_1 * ... * d_n``."""
        total = 1
        for d in self.sizes:
            total *= d
        return total

    def address(self, subscripts: Sequence[int]) -> int:
        """Eq. 1: ``a + SUM (i_k - l_k) * stride_k``. Raises ``OracleError`` for a subscript outside its bounds."""
        if len(subscripts) != len(self.lowers):
            raise OracleError(f"array {self.name} has {len(self.lowers)} dimensions, got {len(subscripts)} subscripts")
        address = self.base
        for k, (i, l, u, stride) in enumerate(zip(subscripts, self.lowers, self.uppers, self.strides), start=1):
            if not l <= i <= u:
                raise OracleError(f"{self.name}: subscript {k} = {i} is outside {l}..{u}")
            address += (i - l) * stride
        return address

    def addresses(self) -> range:
        """Addresses of all elements: ``base .. base + size - 1``."""
        return range(self.base, self.base + self.size)


def init_value(address: int) -> int:
    """Initial content of the cell at ``address``: ``(31 * address + 7) mod 1009`` (mathematical modulo)."""
    return (INIT_MULTIPLIER * address + INIT_OFFSET) % INIT_MODULUS


def initial_memory(arrays: Sequence[Array]) -> dict[int, int]:
    """Address -> initial value for every cell of every declared array."""
    memory = {}
    for array in arrays:
        for address in array.addresses():
            memory[address] = init_value(address)
    return memory


# -- program description -----------------------------------------------------------------------


@dataclass(frozen=True)
class Access:
    """One array access: ``kind`` is ``"R"`` or ``"W"``; ``subscripts`` has one entry per dimension."""

    kind: str
    array: str
    subscripts: tuple[Subscript, ...]


@dataclass(frozen=True)
class Statement:
    """A statement as the ordered list of its array accesses."""

    accesses: tuple[Access, ...]

    def __init__(self, accesses: Sequence[Access]):
        object.__setattr__(self, "accesses", tuple(accesses))


@dataclass(frozen=True)
class Loop:
    """``for var = lo to hi do body end`` with constant bounds and step 1."""

    var: str
    lo: int
    hi: int
    body: tuple["Loop | Statement", ...]

    def __init__(self, var: str, lo: int, hi: int, body: Sequence["Loop | Statement"]):
        object.__setattr__(self, "var", var)
        object.__setattr__(self, "lo", lo)
        object.__setattr__(self, "hi", hi)
        object.__setattr__(self, "body", tuple(body))


def read(array: str, *subscripts: Subscript) -> Access:
    """A read access ``array[subscripts]``."""
    return Access(READ, array, tuple(subscripts))


def write(array: str, *subscripts: Subscript) -> Access:
    """A write access ``array[subscripts]``."""
    return Access(WRITE, array, tuple(subscripts))


def assign(target: Access, *reads: Access) -> Statement:
    """``target = E;`` where ``reads`` are the accesses of ``E`` in evaluation order: reads first, then the store."""
    if target.kind != WRITE:
        raise OracleError("the target of an assignment must be a write access")
    return Statement([*reads, target])


def linear(constant: int = 0, **coefficients: int) -> Callable[[Mapping[str, int]], int]:
    """The subscript ``constant + SUM coefficient * variable``, for example ``linear(1, i=2)`` is ``2*i + 1``."""

    def evaluate(env: Mapping[str, int]) -> int:
        total = constant
        for name, coefficient in coefficients.items():
            if name not in env:
                raise OracleError(f"loop variable {name!r} is not in scope")
            total += coefficient * env[name]
        return total

    return evaluate


def _value(subscript: Subscript, env: Mapping[str, int]) -> int:
    if isinstance(subscript, bool):
        raise OracleError("a subscript must be an int, a loop variable name or a function")
    if isinstance(subscript, int):
        return subscript
    if isinstance(subscript, str):
        if subscript not in env:
            raise OracleError(f"loop variable {subscript!r} is not in scope")
        return env[subscript]
    return subscript(env)


# -- trace -------------------------------------------------------------------------------------


def expected_trace(arrays: Sequence[Array], body: Sequence["Loop | Statement"]) -> list[tuple[str, int]]:
    """The ordered ``(kind, address)`` trace of ``body`` over the declared ``arrays``.

    Walks the loops in plain Python and applies Eq. 1 to every access in
    source order. Raises ``OracleError`` for an unknown array, an unbound
    loop variable or a subscript outside its dimension's bounds.
    """
    by_name = {}
    for array in arrays:
        if array.name in by_name:
            raise OracleError(f"array {array.name} is declared twice")
        by_name[array.name] = array
    trace: list[tuple[str, int]] = []

    def run(items: Sequence["Loop | Statement"], env: dict[str, int]) -> None:
        for item in items:
            if isinstance(item, Loop):
                if item.var in env:
                    raise OracleError(f"loop variable {item.var!r} is already in use")
                for value in range(item.lo, item.hi + 1):
                    run(item.body, {**env, item.var: value})
            else:
                for access in item.accesses:
                    if access.array not in by_name:
                        raise OracleError(f"unknown array {access.array!r}")
                    subscripts = [_value(s, env) for s in access.subscripts]
                    trace.append((access.kind, by_name[access.array].address(subscripts)))

    run(body, {})
    return trace
