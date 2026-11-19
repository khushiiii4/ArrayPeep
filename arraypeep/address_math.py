"""Pure formulas: d_i, C, CONSPART, strides and array size.

Notation (docs/notation_cheatsheet.md, paper Eq. 1 and Eq. 2), for an array
``A[l1:u1, ..., ln:un]`` stored row-major at base address ``a``:

* ``d_k = u_k - l_k + 1``                       size of dimension k
* ``stride_k = d_(k+1) * ... * d_n``            (``stride_n = 1``)
* ``size = d_1 * ... * d_n``                    number of elements
* ``C = (...((l1*d2 + l2)*d3 + l3)...)*dn + ln``
* ``CONSPART = a - C``
* ``VARPART = (...((i1*d2 + i2)*d3 + i3)...)*dn + in``
* ``D = a + SUM (i_k - l_k) * stride_k = CONSPART + VARPART``   (Eq. 1 = Eq. 2)

All functions are pure. Integers are Python integers, so there is no overflow
and negative bounds, subscripts and addresses are fine. This module imports
only the standard library: it knows nothing about the parser, the symbol table
or the quadruples, so it can be tested alone.

Errors: ``TypeError`` for a value that is not an ``int`` (``bool`` is rejected
too), ``ValueError`` for a value that is out of range or for sequences that do
not fit together. The symbol table translates these into its own errors.
"""
from collections.abc import Sequence


def _check_int(value: object, what: str) -> int:
    """Return ``value`` if it is a genuine ``int``; reject ``bool`` and others."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an integer constant, got {value!r} "
            "(variable-bound arrays are not supported)"
        )
    return value


def _check_ints(values: Sequence[int], what: str) -> None:
    """Require a non-empty sequence of integers."""
    if len(values) == 0:
        raise ValueError(f"{what} must contain at least one dimension")
    for v in values:
        _check_int(v, what)


def _check_sizes(sizes: Sequence[int]) -> None:
    """Require a non-empty sequence of integer dimension sizes, each >= 1."""
    _check_ints(sizes, "dimension size")
    for d in sizes:
        if d < 1:
            raise ValueError(f"dimension size must be at least 1, got {d}")


def _check_same_length(**named: Sequence[int]) -> None:
    """Require all given sequences to have the same length."""
    lengths = {name: len(seq) for name, seq in named.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"sequences must have the same length, got {lengths}")


def dim_size(lower: int, upper: int) -> int:
    """Number of values of a dimension: ``d = upper - lower + 1`` (paper §3.1).

    The paper also prints ``- 1`` in Section 2; ``+ 1`` is correct because the
    range ``lower..upper`` holds ``upper - lower + 1`` values. A dimension with
    ``lower == upper`` has size 1.

    Raises ``ValueError`` if ``upper < lower``.
    """
    _check_int(lower, "lower bound")
    _check_int(upper, "upper bound")
    if upper < lower:
        raise ValueError(
            f"upper bound {upper} is less than lower bound {lower}"
        )
    return upper - lower + 1


def info_constant_c(lowers: Sequence[int], sizes: Sequence[int]) -> int:
    """Constant ``C`` of the array information vector (paper Eq. 2).

    Nested (Horner) form: ``C = (...((l1*d2 + l2)*d3 + l3)...)*dn + ln``.
    It folds every lower-bound term of Eq. 1 into one number. For one
    dimension ``C = l1``. ``sizes[0]`` (``d1``) is not used by the formula but
    must be supplied so that both sequences describe the same array.
    """
    _check_ints(lowers, "lower bound")
    _check_sizes(sizes)
    _check_same_length(lowers=lowers, sizes=sizes)
    c = lowers[0]
    for k in range(1, len(lowers)):
        c = c * sizes[k] + lowers[k]
    return c


def conspart(base: int, c: int) -> int:
    """Constant part of every address: ``CONSPART = a - C`` (paper Eq. 2).

    It is the address of the notional element ``A[0, ..., 0]``, known at
    compile time; it may be negative or lie outside the array.
    """
    _check_int(base, "base address")
    _check_int(c, "constant C")
    return base - c


def strides(sizes: Sequence[int]) -> tuple[int, ...]:
    """Row-major strides: ``stride_k = d_(k+1) * ... * d_n``, ``stride_n = 1``.

    This is the same quantity as the paper's boundary products (reversed
    labelling). Example: sizes ``(4, 5, 6)`` give ``(30, 6, 1)``.
    """
    _check_sizes(sizes)
    result = [1] * len(sizes)
    for k in range(len(sizes) - 2, -1, -1):
        result[k] = result[k + 1] * sizes[k + 1]
    return tuple(result)


def array_size(sizes: Sequence[int]) -> int:
    """Number of elements: ``size = d_1 * d_2 * ... * d_n``.

    The array occupies addresses ``a .. a + size - 1``.
    """
    _check_sizes(sizes)
    total = 1
    for d in sizes:
        total *= d
    return total


def varpart(subscripts: Sequence[int], sizes: Sequence[int]) -> int:
    """Variable part of an address (paper Eq. 2), nested form.

    ``VARPART = (...((i1*d2 + i2)*d3 + i3)...)*dn + in``. For one dimension it
    is just the subscript (no arithmetic). The subscripts are *not* checked
    against the bounds: the identity ``CONSPART + VARPART = Eq. 1`` holds for
    any integers, and the run-time check is on the final address.
    """
    _check_ints(subscripts, "subscript")
    _check_sizes(sizes)
    _check_same_length(subscripts=subscripts, sizes=sizes)
    v = subscripts[0]
    for k in range(1, len(subscripts)):
        v = v * sizes[k] + subscripts[k]
    return v


def direct_address(
    base: int,
    lowers: Sequence[int],
    sizes: Sequence[int],
    subscripts: Sequence[int],
) -> int:
    """Address of ``A[i1, ..., in]`` by the direct row-major formula (Eq. 1).

    ``D = a + (i1-l1)*d2*...*dn + ... + (i(n-1)-l(n-1))*dn + (in-ln)``, i.e.
    ``D = a + SUM (i_k - l_k) * stride_k``. Equal to
    ``conspart(base, C) + varpart(...)`` for every subscript list. The
    subscripts are not range-checked (see ``varpart``).
    """
    _check_int(base, "base address")
    _check_ints(lowers, "lower bound")
    _check_ints(subscripts, "subscript")
    _check_sizes(sizes)
    _check_same_length(lowers=lowers, sizes=sizes, subscripts=subscripts)
    return base + sum(
        (i - lo) * s for i, lo, s in zip(subscripts, lowers, strides(sizes))
    )
