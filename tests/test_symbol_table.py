"""Tests for the whole of Phase 2: ``arraypeep.address_math`` and ``arraypeep.symbol_table``.

Address math (Eq. 1 and Eq. 2): dimension size ``d = u - l + 1``, ``C``,
``CONSPART``, ``VARPART``, row-major strides, array size and the direct
address, including negative bounds, size-one dimensions and out-of-range
subscripts, plus input validation.

Array information vector (``ArrayInfo``): the five arrays of the roadmap's
Phase 2 table and extra arrays, immutability, 1 to 4 dimensions, and rejection
of zero or too many dimensions (S6), ``upper < lower`` (S5, reported before S6
by design decision 44) and non-integer values.

Symbol table (``SymbolTable``): array registration, duplicates (S4), overlapping
and adjacent address regions (S7), scalars and loop variables, name conflicts
(S3, S10), the error classes, and the rule order of design decision 44.

Printer: the information-vector layout of docs/quad_spec.md section 9.1,
negative numbers, several arrays, the empty table, column alignment.

Cross-checks: exhaustive address identity ``CONSPART + VARPART == Eq. 1`` with
first and last element and contiguity, and module independence (no lexer,
parser or AST imports).

Expected values come from the roadmap and the Phase 1 hand examples, not from
the code under test. The Eq. 1 reference used for the identity test is written
here in its explicit product form and does not use ``address_math``.
"""
import ast
import itertools
import pathlib
import unittest

import dataclasses

from arraypeep import address_math as am
from arraypeep import symbol_table as st

# (name, base, lowers, uppers, d, C, CONSPART, strides, size) - roadmap Phase 2 table.
ROADMAP_ARRAYS = [
    ("V0", 100, (0,), (9,), (10,), 0, 100, (1,), 10),
    ("V5", 500, (5,), (14,), (10,), 5, 495, (1,), 10),
    ("A", 1000, (1, 1), (10, 20), (10, 20), 21, 979, (20, 1), 200),
    ("M", 0, (0, 0, 0), (3, 4, 5), (4, 5, 6), 0, 0, (30, 6, 1), 120),
    ("N", 200, (-5, -3), (5, 3), (11, 7), -38, 238, (7, 1), 77),
]

# Extra arrays: roadmap section 5.3 declarations and edge cases.
EXTRA_ARRAYS = [
    ("S", 300, (1, 1), (8, 8), (8, 8), 9, 291, (8, 1), 64),
    ("U", 50, (1, 1), (1, 5), (1, 5), 6, 44, (5, 1), 5),
    ("H", 0, (0, 0, 0, 0), (1, 1, 1, 2), (2, 2, 2, 3), 0, 0, (12, 6, 3, 1), 24),
    ("X", 0, (1, 1), (4, 4), (4, 4), 5, -5, (4, 1), 16),
    ("Y", 16, (1, 1), (4, 4), (4, 4), 5, 11, (4, 1), 16),
    ("Z", 32, (1, 1), (4, 4), (4, 4), 5, 27, (4, 1), 16),
    ("C10", 0, (1, 1), (4, 5), (4, 5), 6, -6, (5, 1), 20),
    ("ONE", 7, (3,), (3,), (1,), 3, 4, (1,), 1),
    ("NEGBASE", -50, (-2, 0), (1, 2), (4, 3), -6, -44, (3, 1), 12),
]


def eq1_reference(base, lowers, sizes, subscripts):
    """Eq. 1 in its explicit product form, independent of address_math."""
    n = len(lowers)
    total = base
    for k in range(n):
        term = subscripts[k] - lowers[k]
        for m in range(k + 1, n):
            term *= sizes[m]
        total += term
    return total


class DimSizeTests(unittest.TestCase):
    def test_plus_one(self) -> None:
        self.assertEqual(am.dim_size(1, 10), 10)
        self.assertEqual(am.dim_size(5, 14), 10)
        self.assertEqual(am.dim_size(0, 9), 10)

    def test_negative_bounds(self) -> None:
        self.assertEqual(am.dim_size(-5, 5), 11)
        self.assertEqual(am.dim_size(-3, 3), 7)
        self.assertEqual(am.dim_size(-10, -1), 10)

    def test_size_one(self) -> None:
        self.assertEqual(am.dim_size(1, 1), 1)
        self.assertEqual(am.dim_size(-4, -4), 1)

    def test_upper_below_lower_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            am.dim_size(5, 4)
        self.assertIn("upper bound 4 is less than lower bound 5", str(ctx.exception))
        with self.assertRaises(ValueError):
            am.dim_size(0, -1)

    def test_non_integer_rejected(self) -> None:
        for lo, hi in [("1", 5), (1, "n"), (1.0, 5), (1, 5.5), (True, 5), (1, None)]:
            with self.subTest(lo=lo, hi=hi):
                with self.assertRaises(TypeError):
                    am.dim_size(lo, hi)  # type: ignore[arg-type]

    def test_type_error_mentions_variable_arrays(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            am.dim_size(1, "n")  # type: ignore[arg-type]
        self.assertIn("variable-bound arrays are not supported", str(ctx.exception))


class RoadmapVectorTests(unittest.TestCase):
    """The five arrays of the roadmap's Phase 2 table, plus extra arrays."""

    def check(self, row) -> None:
        name, base, lows, ups, d, c, cp, st, size = row
        with self.subTest(array=name):
            sizes = tuple(am.dim_size(lo, up) for lo, up in zip(lows, ups))
            self.assertEqual(sizes, d)
            self.assertEqual(am.info_constant_c(lows, sizes), c)
            self.assertEqual(am.conspart(base, c), cp)
            self.assertEqual(am.strides(sizes), st)
            self.assertEqual(am.array_size(sizes), size)

    def test_roadmap_table(self) -> None:
        for row in ROADMAP_ARRAYS:
            self.check(row)

    def test_extra_arrays(self) -> None:
        for row in EXTRA_ARRAYS:
            self.check(row)

    def test_hand_example_addresses(self) -> None:
        # Phase 1 hand examples: Eq. 1 and CONSPART + VARPART both give these.
        cases = [
            (1000, (1, 1), (10, 20), (2, 3), 1022, 979, 43),
            (500, (5,), (10,), (9,), 504, 495, 9),
            (200, (-5, -3), (11, 7), (-5, -3), 200, 238, -38),
            (1000, (1, 1), (10, 20), (3, 4), 1043, 979, 64),  # cheat-sheet example
        ]
        for base, lows, sizes, idx, addr, cp, vp in cases:
            with self.subTest(idx=idx):
                self.assertEqual(am.direct_address(base, lows, sizes, idx), addr)
                self.assertEqual(
                    am.conspart(base, am.info_constant_c(lows, sizes)), cp
                )
                self.assertEqual(am.varpart(idx, sizes), vp)
                self.assertEqual(cp + vp, addr)


class CFormulaTests(unittest.TestCase):
    def test_one_dimension_is_lower_bound(self) -> None:
        self.assertEqual(am.info_constant_c((7,), (3,)), 7)
        self.assertEqual(am.info_constant_c((-7,), (3,)), -7)

    def test_two_dimensions(self) -> None:
        self.assertEqual(am.info_constant_c((1, 1), (10, 20)), 1 * 20 + 1)
        self.assertEqual(am.info_constant_c((-5, -3), (11, 7)), -5 * 7 - 3)

    def test_three_and_four_dimensions(self) -> None:
        self.assertEqual(am.info_constant_c((1, 2, 3), (4, 5, 6)), (1 * 5 + 2) * 6 + 3)
        self.assertEqual(
            am.info_constant_c((1, 2, 3, 4), (2, 3, 4, 5)),
            ((1 * 3 + 2) * 4 + 3) * 5 + 4,
        )

    def test_c_equals_sum_of_lower_times_stride(self) -> None:
        for _, _, lows, _, d, c, _, st, _ in ROADMAP_ARRAYS + EXTRA_ARRAYS:
            self.assertEqual(c, sum(lo * s for lo, s in zip(lows, st)))


class StrideAndSizeTests(unittest.TestCase):
    def test_strides(self) -> None:
        self.assertEqual(am.strides((10, 20)), (20, 1))
        self.assertEqual(am.strides((4, 5, 6)), (30, 6, 1))
        self.assertEqual(am.strides((7,)), (1,))
        self.assertEqual(am.strides((2, 2, 2, 3)), (12, 6, 3, 1))

    def test_size_one_dimension(self) -> None:
        self.assertEqual(am.strides((1, 5)), (5, 1))
        self.assertEqual(am.strides((5, 1)), (1, 1))
        self.assertEqual(am.array_size((1, 1, 1)), 1)

    def test_array_size(self) -> None:
        self.assertEqual(am.array_size((10, 20)), 200)
        self.assertEqual(am.array_size((4, 5, 6)), 120)

    def test_unbounded_integers(self) -> None:
        big = 10**30
        self.assertEqual(am.array_size((big, big)), big * big)
        self.assertEqual(am.strides((big, big)), (big, 1))
        self.assertEqual(am.conspart(-big, big), -2 * big)

    def test_invalid_sizes(self) -> None:
        for bad in [(), (0,), (3, -1), (2, 0, 2)]:
            with self.subTest(sizes=bad):
                with self.assertRaises(ValueError):
                    am.strides(bad)
                with self.assertRaises(ValueError):
                    am.array_size(bad)


class IdentityTests(unittest.TestCase):
    """CONSPART + VARPART == Eq. 1 for every element of every array."""

    def test_every_element(self) -> None:
        for name, base, lows, ups, d, _, _, _, size in ROADMAP_ARRAYS + EXTRA_ARRAYS:
            with self.subTest(array=name):
                c = am.info_constant_c(lows, d)
                cp = am.conspart(base, c)
                ranges = [range(lo, up + 1) for lo, up in zip(lows, ups)]
                addrs = []
                for idx in itertools.product(*ranges):
                    direct = am.direct_address(base, lows, d, idx)
                    self.assertEqual(direct, eq1_reference(base, lows, d, idx))
                    self.assertEqual(direct, cp + am.varpart(idx, d))
                    addrs.append(direct)
                self.assertEqual(len(addrs), size)
                # first element at a, last at a + size - 1, row-major contiguous
                self.assertEqual(addrs[0], base)
                self.assertEqual(addrs[-1], base + size - 1)
                self.assertEqual(addrs, list(range(base, base + size)))

    def test_subscripts_outside_bounds_still_satisfy_identity(self) -> None:
        # A[1, 25] on A[1:10,1:20]@1000 has the address of A[2, 5] (quad_spec 4.3).
        lows, d = (1, 1), (10, 20)
        c = am.info_constant_c(lows, d)
        for idx in [(1, 25), (0, 0), (-3, 40), (11, 21)]:
            with self.subTest(idx=idx):
                self.assertEqual(
                    am.direct_address(1000, lows, d, idx),
                    am.conspart(1000, c) + am.varpart(idx, d),
                )
        self.assertEqual(am.direct_address(1000, lows, d, (1, 25)), 1024)
        self.assertEqual(am.direct_address(1000, lows, d, (2, 5)), 1024)

    def test_varpart_one_dimension_is_the_subscript(self) -> None:
        self.assertEqual(am.varpart((9,), (10,)), 9)
        self.assertEqual(am.varpart((-4,), (10,)), -4)

    def test_linear_example_address_formula(self) -> None:
        # T04: A[2*i+1, j-1], address = 998 + 40*i + j (hand_examples.md, Section 4).
        lows, d = (1, 1), (10, 20)
        addrs = [
            am.direct_address(1000, lows, d, (2 * i + 1, j - 1))
            for i in range(1, 5)
            for j in range(2, 21)
        ]
        self.assertEqual(len(addrs), 76)
        self.assertEqual((addrs[0], addrs[-1]), (1040, 1178))
        self.assertEqual(
            addrs, [998 + 40 * i + j for i in range(1, 5) for j in range(2, 21)]
        )


class InputValidationTests(unittest.TestCase):
    def test_length_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            am.info_constant_c((1, 1), (10,))
        with self.assertRaises(ValueError):
            am.varpart((1, 2, 3), (10, 20))
        with self.assertRaises(ValueError):
            am.direct_address(0, (1, 1), (10, 20), (1,))

    def test_empty_sequences(self) -> None:
        with self.assertRaises(ValueError):
            am.info_constant_c((), ())
        with self.assertRaises(ValueError):
            am.varpart((), ())

    def test_non_integer_inputs(self) -> None:
        with self.assertRaises(TypeError):
            am.conspart(1000.0, 21)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            am.conspart(1000, True)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            am.info_constant_c((1, "x"), (10, 20))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            am.varpart((1, 2.5), (10, 20))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            am.direct_address("0", (1,), (10,), (1,))  # type: ignore[arg-type]

    def test_inputs_not_modified_and_results_are_ints(self) -> None:
        lows, sizes = [1, 1], [10, 20]
        self.assertIsInstance(am.strides(sizes), tuple)
        am.info_constant_c(lows, sizes)
        self.assertEqual((lows, sizes), ([1, 1], [10, 20]))


class ModuleIndependenceTests(unittest.TestCase):
    """Roadmap Phase 2: address_math must be testable alone."""

    def test_imports_only_the_standard_library(self) -> None:
        path = pathlib.Path(am.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for mod in imported:
            self.assertNotIn("arraypeep", mod)
            self.assertEqual(mod.split(".")[0], "collections")


def bounds_of(lowers, uppers):
    """Build the ``bounds`` argument [(l1, u1), ...] from two tuples."""
    return list(zip(lowers, uppers))


class ArrayInfoRoadmapTests(unittest.TestCase):
    """Fields of the record for the five arrays of the roadmap's Phase 2 table."""

    def test_all_fields_for_roadmap_arrays(self) -> None:
        for name, base, lows, ups, d, c, cp, stride, size in ROADMAP_ARRAYS:
            with self.subTest(array=name):
                info = st.ArrayInfo(name, bounds_of(lows, ups), base)
                self.assertEqual(info.name, name)
                self.assertEqual(info.n, len(lows))
                self.assertEqual(info.lowers, lows)
                self.assertEqual(info.uppers, ups)
                self.assertEqual(info.sizes, d)
                self.assertEqual(info.c, c)
                self.assertEqual(info.base, base)
                self.assertEqual(info.conspart, cp)
                self.assertEqual(info.strides, stride)
                self.assertEqual(info.size, size)
                self.assertEqual(info.element_type, "int")
                self.assertEqual(info.last_address, base + size - 1)

    def test_extra_arrays_match_expected(self) -> None:
        for name, base, lows, ups, d, c, cp, stride, size in EXTRA_ARRAYS:
            with self.subTest(array=name):
                info = st.ArrayInfo(name, bounds_of(lows, ups), base)
                self.assertEqual(
                    (info.sizes, info.c, info.conspart, info.strides, info.size),
                    (d, c, cp, stride, size),
                )

    def test_a_vector_matches_phase_1_spec(self) -> None:
        # quad_spec.md 9.1 / roadmap 5.2: A[1:10,1:20] at 1000.
        info = st.ArrayInfo("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual((info.n, info.c, info.conspart, info.size), (2, 21, 979, 200))
        self.assertEqual((info.sizes, info.strides), ((10, 20), (20, 1)))

    def test_fields_agree_with_address_math(self) -> None:
        # The record stores what address_math computes: first element at a,
        # CONSPART + VARPART of the lower bounds is the base address.
        for name, base, lows, ups, *_ in ROADMAP_ARRAYS + EXTRA_ARRAYS:
            with self.subTest(array=name):
                info = st.ArrayInfo(name, bounds_of(lows, ups), base)
                self.assertEqual(info.conspart + am.varpart(info.lowers, info.sizes), info.base)
                self.assertEqual(
                    info.conspart + am.varpart(info.uppers, info.sizes), info.last_address
                )


class ArrayInfoEdgeCaseTests(unittest.TestCase):
    def test_negative_bounds(self) -> None:
        info = st.ArrayInfo("N", [(-5, 5), (-3, 3)], 200)
        self.assertEqual((info.lowers, info.uppers), ((-5, -3), (5, 3)))
        self.assertEqual((info.sizes, info.c, info.conspart), ((11, 7), -38, 238))
        self.assertEqual(info.last_address, 276)

    def test_all_negative_bounds(self) -> None:
        info = st.ArrayInfo("Q", [(-10, -1)], 0)
        self.assertEqual((info.sizes, info.c, info.conspart), ((10,), -10, 10))

    def test_negative_base_address(self) -> None:
        info = st.ArrayInfo("W", [(-2, 1), (0, 2)], -50)
        self.assertEqual(info.base, -50)
        self.assertEqual((info.sizes, info.c, info.conspart), ((4, 3), -6, -44))
        self.assertEqual(info.last_address, -50 + 12 - 1)

    def test_size_one_dimensions(self) -> None:
        one = st.ArrayInfo("ONE", [(3, 3)], 7)
        self.assertEqual((one.sizes, one.strides, one.size, one.c, one.conspart), ((1,), (1,), 1, 3, 4))
        self.assertEqual(one.last_address, one.base)
        u = st.ArrayInfo("U", [(1, 1), (1, 5)], 50)
        self.assertEqual((u.sizes, u.strides, u.size, u.c, u.conspart), ((1, 5), (5, 1), 5, 6, 44))
        col = st.ArrayInfo("COL", [(1, 5), (1, 1)], 0)
        self.assertEqual((col.sizes, col.strides, col.size), ((5, 1), (1, 1), 5))

    def test_four_dimensions(self) -> None:
        info = st.ArrayInfo("H", [(0, 1), (0, 1), (0, 1), (0, 2)], 0)
        self.assertEqual(info.n, 4)
        self.assertEqual((info.sizes, info.strides, info.size), ((2, 2, 2, 3), (12, 6, 3, 1), 24))
        self.assertEqual((info.c, info.conspart), (0, 0))

    def test_one_to_four_dimensions_accepted(self) -> None:
        for n in range(1, st.MAX_DIMENSIONS + 1):
            with self.subTest(n=n):
                self.assertEqual(st.ArrayInfo("D", [(0, 1)] * n, 0).n, n)
        self.assertEqual(st.MAX_DIMENSIONS, 4)

    def test_large_values(self) -> None:
        big = 10**25
        info = st.ArrayInfo("BIG", [(-big, big)], -big)
        self.assertEqual(info.sizes, (2 * big + 1,))
        self.assertEqual(info.conspart, -big + big)

    def test_accepts_any_sequence_kind(self) -> None:
        a = st.ArrayInfo("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(a, st.ArrayInfo("A", ((1, 10), (1, 20)), 1000))
        self.assertEqual(a, st.ArrayInfo("A", [[1, 10], [1, 20]], 1000))


class ArrayInfoImmutabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.info = st.ArrayInfo("A", [(1, 10), (1, 20)], 1000)

    def test_fields_are_tuples_or_ints(self) -> None:
        for field in ("lowers", "uppers", "sizes", "strides"):
            self.assertIsInstance(getattr(self.info, field), tuple, field)
        for field in ("n", "c", "base", "conspart", "size"):
            self.assertIs(type(getattr(self.info, field)), int, field)

    def test_cannot_assign_or_add_fields(self) -> None:
        for field in ("name", "n", "lowers", "c", "base", "conspart", "strides", "size", "element_type"):
            with self.subTest(field=field):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(self.info, field, 0)
        with self.assertRaises((AttributeError, TypeError)):
            self.info.extra = 1  # type: ignore[attr-defined]

    def test_cannot_delete_fields(self) -> None:
        with self.assertRaises(dataclasses.FrozenInstanceError):
            del self.info.size

    def test_equal_and_hashable(self) -> None:
        same = st.ArrayInfo("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(self.info, same)
        self.assertEqual(hash(self.info), hash(same))
        self.assertNotEqual(self.info, st.ArrayInfo("A", [(1, 10), (1, 20)], 1001))
        self.assertNotEqual(self.info, st.ArrayInfo("B", [(1, 10), (1, 20)], 1000))
        self.assertEqual(len({self.info, same}), 1)

    def test_not_affected_by_later_changes_to_the_input(self) -> None:
        bounds = [[1, 10], [1, 20]]
        info = st.ArrayInfo("A", bounds, 1000)
        bounds[0][1] = 99
        bounds.append([0, 0])
        self.assertEqual((info.uppers, info.n, info.size), ((10, 20), 2, 200))


class ArrayInfoRejectionTests(unittest.TestCase):
    def assertRejected(self, args, expected_text=None) -> None:
        with self.assertRaises(st.DeclarationError) as ctx:
            st.ArrayInfo(*args)
        self.assertIsInstance(ctx.exception, st.ArrayPeepError)
        if expected_text is not None:
            self.assertIn(expected_text, str(ctx.exception))

    def test_zero_dimensions(self) -> None:
        self.assertRejected(("B", [], 0), "array 'B' has no dimensions; at least 1 is required")
        self.assertRejected(("B", (), 0), "no dimensions")

    def test_five_dimensions_s6(self) -> None:
        self.assertRejected(
            ("B", [(0, 1)] * 5, 0), "array 'B' has 5 dimensions; at most 4 are supported"
        )
        self.assertRejected(("B", [(0, 1)] * 9, 0), "has 9 dimensions")

    def test_upper_below_lower_s5(self) -> None:
        self.assertRejected(
            ("B", [(5, 4)], 0),
            "upper bound 4 is less than lower bound 5 in dimension 1 of array 'B'",
        )
        self.assertRejected(("B", [(1, 3), (0, -1)], 0), "in dimension 2 of array 'B'")
        self.assertRejected(("B", [(0, -10)], 0), "upper bound -10 is less than lower bound 0")

    def test_equal_bounds_are_legal(self) -> None:
        self.assertEqual(st.ArrayInfo("B", [(4, 4)], 0).size, 1)

    def test_non_integer_bounds(self) -> None:
        for bad in ("n", 1.0, 2.5, None, True, False, [1], (1, 2)):
            with self.subTest(upper=bad):
                self.assertRejected(("B", [(1, bad)], 0), "dimension 1")
            with self.subTest(lower=bad):
                self.assertRejected(("B", [(bad, 5)], 0), "dimension 1")

    def test_non_integer_bound_message_mentions_variable_arrays(self) -> None:
        self.assertRejected(
            ("B", [(1, "n")], 0), "variable-bound arrays are not supported"
        )

    def test_error_names_the_failing_dimension(self) -> None:
        self.assertRejected(("B", [(1, 2), (1, "m")], 0), "dimension 2")

    def test_non_integer_base_address(self) -> None:
        for bad in ("0", 1000.0, None, True, False):
            with self.subTest(base=bad):
                self.assertRejected(("B", [(1, 2)], bad), "base address")

    def test_bad_bounds_structure(self) -> None:
        self.assertRejected(("B", "ab", 0), "sequence of (lower, upper) pairs")
        self.assertRejected(("B", 5, 0), "sequence of (lower, upper) pairs")
        self.assertRejected(("B", None, 0), "sequence of (lower, upper) pairs")
        self.assertRejected(("B", [(1,)], 0), "(lower, upper) pair")
        self.assertRejected(("B", [(1, 2, 3)], 0), "(lower, upper) pair")
        self.assertRejected(("B", [5], 0), "(lower, upper) pair")
        self.assertRejected(("B", ["ab"], 0), "(lower, upper) pair")

    def test_bad_name(self) -> None:
        self.assertRejected(("", [(1, 2)], 0), "array name")
        self.assertRejected((None, [(1, 2)], 0), "array name")
        self.assertRejected((5, [(1, 2)], 0), "array name")

    S5_FIRST = "upper bound 1 is less than lower bound 2 in dimension 1 of array 'B'"

    def test_s5_is_reported_before_s6(self) -> None:
        # Design decision 44: the lowest-numbered rule wins, and S5 < S6.
        self.assertRejected(("B", [(2, 1)] * 5, 0), self.S5_FIRST)
        with self.assertRaises(st.DeclarationError) as ctx:
            st.ArrayInfo("B", [(2, 1)] * 5, 0)
        self.assertNotIn("dimensions; at most", str(ctx.exception))

    def test_s5_before_s6_names_the_first_bad_dimension(self) -> None:
        bounds = [(0, 1), (0, 1), (5, 4), (0, 1), (9, 3), (0, 1)]  # 6 dims, bad: 3 and 5
        self.assertRejected(
            ("B", bounds, 0), "upper bound 4 is less than lower bound 5 in dimension 3 of array 'B'"
        )

    def test_s6_still_reported_when_all_bounds_are_valid(self) -> None:
        self.assertRejected(
            ("B", [(0, 1)] * 5, 0), "array 'B' has 5 dimensions; at most 4 are supported"
        )
        self.assertRejected(("B", [(3, 3)] * 6, 0), "array 'B' has 6 dimensions")  # u == l is valid

    def test_s5_wins_over_s6_through_the_symbol_table_too(self) -> None:
        table = st.SymbolTable()
        with self.assertRaises(st.DeclarationError) as ctx:
            table.declare_array("B", [(2, 1)] * 5, 0, line=4)
        self.assertEqual(str(ctx.exception), self.S5_FIRST)
        self.assertEqual(ctx.exception.line, 4)
        self.assertEqual(table.arrays, ())

    def test_other_errors_keep_their_order_relative_to_s6(self) -> None:
        # Only S5 moved. A non-integer bound or a malformed pair in a 5-D
        # declaration is still reported after the dimension count, as before.
        self.assertRejected(("B", [(0, "n")] + [(0, 1)] * 4, 0), "5 dimensions")
        self.assertRejected(("B", [(0,)] + [(0, 1)] * 4, 0), "5 dimensions")
        # A well-formed S5 violation is still found when other dimensions are malformed.
        self.assertRejected(("B", [(0, "n"), (3, 2)] + [(0, 1)] * 3, 0), "less than lower bound 3 in dimension 2")
        # Zero dimensions is still its own error.
        self.assertRejected(("B", [], 0), "no dimensions")

    def test_four_dimensions_with_a_bad_bound_is_s5(self) -> None:
        self.assertRejected(("B", [(0, 1)] * 3 + [(5, 4)], 0), "in dimension 4 of array 'B'")


class ErrorClassTests(unittest.TestCase):
    def test_hierarchy_and_message(self) -> None:
        err = st.DeclarationError("boom")
        self.assertIsInstance(err, st.ArrayPeepError)
        self.assertIsInstance(err, Exception)
        self.assertEqual(str(err), "boom")
        self.assertEqual(err.message, "boom")
        self.assertIsNone(err.line)

    def test_optional_line_number(self) -> None:
        err = st.ArrayPeepError("bad", line=6)
        self.assertEqual(err.line, 6)
        self.assertEqual(str(err), "bad")

    def test_declaration_error_chains_the_cause(self) -> None:
        with self.assertRaises(st.DeclarationError) as ctx:
            st.ArrayInfo("B", [(5, 4)], 0)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)


def small(name, base, count=10):
    """Declaration arguments for a 1-D array of ``count`` cells at ``base``."""
    return name, [(0, count - 1)], base


class DeclareArrayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = st.SymbolTable()

    def test_empty_table(self) -> None:
        self.assertEqual(self.table.arrays, ())
        self.assertEqual(self.table.scalars, ())
        self.assertEqual(self.table.loop_variables, ())
        self.assertIsNone(self.table.get_array("A"))
        self.assertIsNone(self.table.kind_of("A"))
        self.assertNotIn("A", self.table)

    def test_declare_returns_the_record_and_stores_it(self) -> None:
        info = self.table.declare_array("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(info, st.ArrayInfo("A", [(1, 10), (1, 20)], 1000))
        self.assertIs(self.table.get_array("A"), info)
        self.assertTrue(self.table.is_array("A"))
        self.assertEqual(self.table.kind_of("A"), st.KIND_ARRAY)
        self.assertIn("A", self.table)
        self.assertEqual(self.table.arrays, (info,))

    def test_roadmap_arrays_in_declaration_order(self) -> None:
        # The roadmap's five sample arrays are not meant to share one program:
        # M occupies 0..119 and overlaps V0 (100..109). The other four do not overlap.
        names = []
        for name, base, lows, ups, *_ in ROADMAP_ARRAYS:
            if name == "M":
                continue
            info = self.table.declare_array(name, bounds_of(lows, ups), base)
            names.append(name)
            self.assertIs(self.table.get_array(name), info)
        self.assertEqual([a.name for a in self.table.arrays], names)
        self.assertEqual(names, ["V0", "V5", "A", "N"])
        with self.assertRaises(st.OverlapError) as ctx:
            self.table.declare_array("M", [(0, 3), (0, 4), (0, 5)], 0)
        self.assertEqual(
            str(ctx.exception),
            "array 'M' (addresses 0..119) overlaps array 'V0' (addresses 100..109)",
        )

    def test_each_roadmap_array_registers_alone(self) -> None:
        for name, base, lows, ups, d, c, cp, stride, size in ROADMAP_ARRAYS:
            with self.subTest(array=name):
                table = st.SymbolTable()
                info = table.declare_array(name, bounds_of(lows, ups), base)
                self.assertEqual(
                    (info.sizes, info.c, info.conspart, info.strides, info.size),
                    (d, c, cp, stride, size),
                )

    def test_lookup_is_case_sensitive(self) -> None:
        self.table.declare_array(*small("A", 0))
        self.assertIsNone(self.table.get_array("a"))
        self.table.declare_array(*small("a", 100))
        self.assertEqual(len(self.table.arrays), 2)

    def test_non_array_names_have_no_record(self) -> None:
        self.table.register_scalar("s")
        self.table.register_loop_variable("i")
        self.assertIsNone(self.table.get_array("s"))
        self.assertIsNone(self.table.get_array("i"))

    def test_invalid_declarations_pass_through_and_leave_table_unchanged(self) -> None:
        for args in [("B", [(5, 4)], 0), ("B", [(0, 1)] * 5, 0), ("B", [(1, "n")], 0), ("B", [], 0)]:
            with self.subTest(args=args):
                with self.assertRaises(st.DeclarationError):
                    self.table.declare_array(*args)
        self.assertEqual(self.table.arrays, ())
        self.assertNotIn("B", self.table)

    def test_bad_names_rejected(self) -> None:
        for bad in ("", None, 5):
            with self.subTest(name=bad):
                with self.assertRaises(st.DeclarationError):
                    self.table.declare_array(bad, [(0, 1)], 0)  # type: ignore[arg-type]
        self.assertEqual(self.table.arrays, ())

    def test_line_number_is_attached_to_array_errors(self) -> None:
        with self.assertRaises(st.DeclarationError) as ctx:
            self.table.declare_array("B", [(5, 4)], 0, line=7)
        self.assertEqual(ctx.exception.line, 7)
        self.assertEqual(
            str(ctx.exception),
            "upper bound 4 is less than lower bound 5 in dimension 1 of array 'B'",
        )

    def test_returned_collections_are_tuples(self) -> None:
        self.table.declare_array(*small("A", 0))
        self.table.register_scalar("s")
        self.table.register_loop_variable("i")
        for coll in (self.table.arrays, self.table.scalars, self.table.loop_variables):
            self.assertIsInstance(coll, tuple)


class DuplicateArrayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = st.SymbolTable()
        self.first = self.table.declare_array("A", [(1, 10), (1, 20)], 1000)

    def test_s4_message(self) -> None:
        with self.assertRaises(st.DuplicateArrayError) as ctx:
            self.table.declare_array("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(str(ctx.exception), "array 'A' is already declared")
        self.assertEqual(ctx.exception.name, "A")
        self.assertIsInstance(ctx.exception, st.DeclarationError)
        self.assertIsInstance(ctx.exception, st.ArrayPeepError)

    def test_duplicate_with_different_shape_or_address(self) -> None:
        for args in [("A", [(0, 1)], 5000), ("A", [(1, 10), (1, 20)], 9999), ("A", [(1, 3)], -40)]:
            with self.subTest(args=args):
                with self.assertRaises(st.DuplicateArrayError):
                    self.table.declare_array(*args)

    def test_original_declaration_is_kept(self) -> None:
        with self.assertRaises(st.DuplicateArrayError):
            self.table.declare_array("A", [(0, 1)], 5000)
        self.assertIs(self.table.get_array("A"), self.first)
        self.assertEqual(self.table.arrays, (self.first,))

    def test_line_number(self) -> None:
        with self.assertRaises(st.DuplicateArrayError) as ctx:
            self.table.declare_array("A", [(0, 1)], 5000, line=12)
        self.assertEqual(ctx.exception.line, 12)

    def test_duplicate_is_reported_before_bound_errors(self) -> None:
        # S4 comes before S5 (design decision 44).
        with self.assertRaises(st.DuplicateArrayError):
            self.table.declare_array("A", [(5, 4)], 0)

    def test_duplicate_is_reported_before_overlap(self) -> None:
        with self.assertRaises(st.DuplicateArrayError):
            self.table.declare_array("A", [(1, 10)], 1000)  # also overlaps A itself


class OverlapTests(unittest.TestCase):
    """A is A[1:10,1:20] at 1000, i.e. the inclusive region 1000..1199."""

    def setUp(self) -> None:
        self.table = st.SymbolTable()
        self.a = self.table.declare_array("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual((self.a.base, self.a.last_address), (1000, 1199))

    def assertOverlap(self, args, message) -> None:
        before = self.table.arrays
        with self.assertRaises(st.OverlapError) as ctx:
            self.table.declare_array(*args)
        self.assertEqual(str(ctx.exception), message)
        self.assertIsInstance(ctx.exception, st.DeclarationError)
        self.assertEqual(self.table.arrays, before)  # table unchanged
        self.assertIsNone(self.table.get_array(args[0]))

    def test_roadmap_example_message_complete_containment(self) -> None:
        # language_spec.md S7: B[1:20] at 1100 lies completely inside A.
        self.assertOverlap(
            ("B", [(1, 20)], 1100),
            "array 'B' (addresses 1100..1119) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_partial_overlap_at_the_end(self) -> None:
        self.assertOverlap(
            ("B", [(1, 50)], 1180),
            "array 'B' (addresses 1180..1229) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_partial_overlap_at_the_start(self) -> None:
        self.assertOverlap(
            ("B", [(0, 29)], 980),
            "array 'B' (addresses 980..1009) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_new_array_contains_existing_array(self) -> None:
        self.assertOverlap(
            ("B", [(0, 999)], 500),
            "array 'B' (addresses 500..1499) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_identical_address_range(self) -> None:
        self.assertOverlap(
            ("B", [(1, 200)], 1000),
            "array 'B' (addresses 1000..1199) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_single_shared_address_at_either_end(self) -> None:
        self.assertOverlap(
            ("B", [(0, 9)], 1190),  # 1190..1199
            "array 'B' (addresses 1190..1199) overlaps array 'A' (addresses 1000..1199)",
        )
        self.assertOverlap(
            ("B", [(0, 0)], 1199),  # exactly the last cell
            "array 'B' (addresses 1199..1199) overlaps array 'A' (addresses 1000..1199)",
        )
        self.assertOverlap(
            ("B", [(0, 0)], 1000),  # exactly the first cell
            "array 'B' (addresses 1000..1000) overlaps array 'A' (addresses 1000..1199)",
        )
        self.assertOverlap(
            ("B", [(0, 10)], 990),  # 990..1000
            "array 'B' (addresses 990..1000) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_adjacent_arrays_are_accepted(self) -> None:
        above = self.table.declare_array("Above", [(0, 9)], 1200)  # 1200..1209
        below = self.table.declare_array("Below", [(0, 799)], 200)  # 200..999
        self.assertEqual((above.base, below.last_address), (1200, 999))
        self.assertEqual([x.name for x in self.table.arrays], ["A", "Above", "Below"])

    def test_one_cell_gap_is_accepted(self) -> None:
        self.table.declare_array("G", [(0, 4)], 1201)
        self.table.declare_array("H", [(0, 4)], 994)  # 994..998

    def test_matrix_example_adjacent_in_every_order(self) -> None:
        # matrix_multiply.ml: X at 0..15, Y at 16..31, Z at 32..47.
        decls = {"X": 0, "Y": 16, "Z": 32}
        for order in itertools.permutations(decls):
            with self.subTest(order=order):
                table = st.SymbolTable()
                for name in order:
                    table.declare_array(name, [(1, 4), (1, 4)], decls[name])
                self.assertEqual([a.name for a in table.arrays], list(order))

    def test_overlap_is_symmetric_but_message_names_the_new_array_first(self) -> None:
        for first, second in [(("P", 0, 10), ("Q", 5, 10)), (("Q", 5, 10), ("P", 0, 10))]:
            with self.subTest(first=first[0]):
                table = st.SymbolTable()
                table.declare_array(*small(*first))
                with self.assertRaises(st.OverlapError) as ctx:
                    table.declare_array(*small(*second))
                self.assertEqual((ctx.exception.name, ctx.exception.other), (second[0], first[0]))

    def test_exact_message_for_both_orders(self) -> None:
        t = st.SymbolTable()
        t.declare_array("P", [(0, 9)], 0)
        with self.assertRaises(st.OverlapError) as ctx:
            t.declare_array("Q", [(0, 9)], 5)
        self.assertEqual(
            str(ctx.exception), "array 'Q' (addresses 5..14) overlaps array 'P' (addresses 0..9)"
        )
        t = st.SymbolTable()
        t.declare_array("Q", [(0, 9)], 5)
        with self.assertRaises(st.OverlapError) as ctx:
            t.declare_array("P", [(0, 9)], 0)
        self.assertEqual(
            str(ctx.exception), "array 'P' (addresses 0..9) overlaps array 'Q' (addresses 5..14)"
        )

    def test_first_overlapping_array_in_declaration_order_is_named(self) -> None:
        self.table.declare_array("C", [(0, 9)], 2000)
        self.assertOverlap(
            ("B", [(0, 1500)], 900),  # 900..2400 overlaps A, C, and more
            "array 'B' (addresses 900..2400) overlaps array 'A' (addresses 1000..1199)",
        )

    def test_failed_declaration_does_not_block_a_later_valid_one(self) -> None:
        with self.assertRaises(st.OverlapError):
            self.table.declare_array("B", [(1, 20)], 1100)
        b = self.table.declare_array("B", [(1, 20)], 1200)
        self.assertIs(self.table.get_array("B"), b)

    def test_negative_base_addresses(self) -> None:
        t = st.SymbolTable()
        t.declare_array("N1", [(0, 9)], -10)  # -10..-1
        t.declare_array("N2", [(0, 9)], 0)  # 0..9, adjacent across zero
        t.declare_array("N3", [(0, 4)], -20)  # -20..-16
        with self.assertRaises(st.OverlapError) as ctx:
            t.declare_array("N4", [(0, 9)], -5)  # -5..4 overlaps N1 and N2
        self.assertEqual(
            str(ctx.exception), "array 'N4' (addresses -5..4) overlaps array 'N1' (addresses -10..-1)"
        )
        with self.assertRaises(st.OverlapError) as ctx:
            t.declare_array("N5", [(0, 9)], -25)  # -25..-16 touches N3's -16
        self.assertEqual(
            str(ctx.exception),
            "array 'N5' (addresses -25..-16) overlaps array 'N3' (addresses -20..-16)",
        )
        t.declare_array("N6", [(0, 4)], -15)  # -15..-11: adjacent to N3 and N1, between them
        self.assertEqual(len(t.arrays), 4)

    def test_negative_bounds_with_positive_base(self) -> None:
        # N[-5:5,-3:3] at 200 occupies 200..276 whatever the bounds are.
        t = st.SymbolTable()
        t.declare_array("N", [(-5, 5), (-3, 3)], 200)
        t.declare_array("L", [(0, 99)], 100)  # 100..199, adjacent
        with self.assertRaises(st.OverlapError):
            t.declare_array("K", [(0, 0)], 276)

    def test_order_of_declaration_does_not_change_acceptance(self) -> None:
        specs = [("A", 1000, 200), ("B", 1200, 50), ("C", 800, 200), ("D", 1250, 5)]
        for order in itertools.permutations(specs):
            with self.subTest(order=[s[0] for s in order]):
                t = st.SymbolTable()
                for name, base, count in order:
                    t.declare_array(*small(name, base, count))
                self.assertEqual(len(t.arrays), 4)
        bad = specs + [("E", 1249, 2)]  # 1249..1250 overlaps B and D
        for order in itertools.permutations(bad):
            with self.subTest(bad_order=[s[0] for s in order]):
                t = st.SymbolTable()
                with self.assertRaises(st.OverlapError):
                    for name, base, count in order:
                        t.declare_array(*small(name, base, count))

    def test_line_number(self) -> None:
        with self.assertRaises(st.OverlapError) as ctx:
            self.table.declare_array("B", [(1, 20)], 1100, line=9)
        self.assertEqual(ctx.exception.line, 9)

    def test_overlap_is_reported_before_loop_variable_conflict(self) -> None:
        # S7 comes before S10.
        self.table.register_loop_variable("i")
        with self.assertRaises(st.OverlapError):
            self.table.declare_array("i", [(1, 20)], 1100)

    def test_overlap_check_uses_only_array_regions(self) -> None:
        # Scalars live outside array memory (quad_spec.md 4.1): they never overlap an array.
        self.table.register_scalar("s")
        self.table.declare_array("B", [(0, 9)], 0)


class ScalarAndLoopVariableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = st.SymbolTable()

    def test_scalar_registration_and_lookup(self) -> None:
        self.table.register_scalar("s")
        self.assertTrue(self.table.is_scalar("s"))
        self.assertFalse(self.table.is_loop_variable("s"))
        self.assertFalse(self.table.is_array("s"))
        self.assertEqual(self.table.kind_of("s"), st.KIND_SCALAR)
        self.assertIn("s", self.table)
        self.assertEqual(self.table.scalars, ("s",))

    def test_loop_variable_registration_and_lookup(self) -> None:
        self.table.register_loop_variable("i")
        self.assertTrue(self.table.is_loop_variable("i"))
        self.assertFalse(self.table.is_scalar("i"))
        self.assertFalse(self.table.is_array("i"))
        self.assertEqual(self.table.kind_of("i"), st.KIND_LOOP_VARIABLE)
        self.assertIn("i", self.table)
        self.assertEqual(self.table.loop_variables, ("i",))

    def test_registration_order_is_kept(self) -> None:
        for name in ("z", "a", "m"):
            self.table.register_scalar(name)
        for name in ("k", "i", "j"):
            self.table.register_loop_variable(name)
        self.assertEqual(self.table.scalars, ("z", "a", "m"))
        self.assertEqual(self.table.loop_variables, ("k", "i", "j"))

    def test_registering_again_is_harmless(self) -> None:
        # A scalar comes into existence at its first mention; sequential loops
        # may reuse a loop-variable name (1d_sum.ml).
        for _ in range(3):
            self.table.register_scalar("s")
            self.table.register_loop_variable("i")
        self.assertEqual(self.table.scalars, ("s",))
        self.assertEqual(self.table.loop_variables, ("i",))

    def test_kinds_do_not_leak_between_tables(self) -> None:
        other = st.SymbolTable()
        self.table.register_scalar("s")
        self.assertNotIn("s", other)

    def test_bad_names(self) -> None:
        for bad in ("", None, 3, ["s"]):
            with self.subTest(name=bad):
                with self.assertRaises(st.DeclarationError):
                    self.table.register_scalar(bad)  # type: ignore[arg-type]
                with self.assertRaises(st.DeclarationError):
                    self.table.register_loop_variable(bad)  # type: ignore[arg-type]
        self.assertEqual((self.table.scalars, self.table.loop_variables), ((), ()))

    def test_case_sensitive_names(self) -> None:
        self.table.register_scalar("s")
        self.table.register_scalar("S")
        self.table.register_loop_variable("I")
        self.table.register_loop_variable("i")
        self.assertEqual(self.table.scalars, ("s", "S"))
        self.assertEqual(self.table.loop_variables, ("I", "i"))

    def test_generated_names_are_not_checked_here(self) -> None:
        # S12 (reserved T1, P2, ...) is a Phase 3 rule (design decision 51).
        self.table.register_scalar("T1")
        self.table.register_loop_variable("P2")
        self.table.declare_array("T3", [(0, 1)], 0)


class NameConflictTests(unittest.TestCase):
    S3 = "array 'A' used without subscripts"
    S10_I = "'i' is used as a loop variable and as an ordinary name"

    def setUp(self) -> None:
        self.table = st.SymbolTable()

    def assertConflict(self, action, message, name, expected_kind) -> None:
        snapshot = (self.table.arrays, self.table.scalars, self.table.loop_variables)
        with self.assertRaises(st.NameConflictError) as ctx:
            action()
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(ctx.exception.name, name)
        self.assertIsInstance(ctx.exception, st.ArrayPeepError)
        self.assertEqual(
            (self.table.arrays, self.table.scalars, self.table.loop_variables), snapshot
        )
        self.assertEqual(self.table.kind_of(name), expected_kind)

    # -- S3: array name vs scalar -----------------------------------------

    def test_scalar_with_an_array_name(self) -> None:
        self.table.declare_array(*small("A", 0))
        self.assertConflict(
            lambda: self.table.register_scalar("A"), self.S3, "A", st.KIND_ARRAY
        )

    def test_array_with_a_scalar_name(self) -> None:
        # "A = 1;" earlier in the program: an array name is an array everywhere.
        self.table.register_scalar("A")
        self.assertConflict(
            lambda: self.table.declare_array(*small("A", 0)), self.S3, "A", st.KIND_SCALAR
        )

    # -- S10: loop variable vs ordinary name ------------------------------

    def test_loop_variable_then_scalar(self) -> None:
        self.table.register_loop_variable("i")
        self.assertConflict(
            lambda: self.table.register_scalar("i"), self.S10_I, "i", st.KIND_LOOP_VARIABLE
        )

    def test_scalar_then_loop_variable(self) -> None:
        self.table.register_scalar("i")
        self.assertConflict(
            lambda: self.table.register_loop_variable("i"), self.S10_I, "i", st.KIND_SCALAR
        )

    def test_loop_variable_then_array(self) -> None:
        self.table.register_loop_variable("i")
        self.assertConflict(
            lambda: self.table.declare_array(*small("i", 0)), self.S10_I, "i", st.KIND_LOOP_VARIABLE
        )

    def test_array_then_loop_variable(self) -> None:
        self.table.declare_array(*small("i", 0))
        self.assertConflict(
            lambda: self.table.register_loop_variable("i"), self.S10_I, "i", st.KIND_ARRAY
        )

    # -- details ----------------------------------------------------------

    def test_conflicts_carry_a_line_number(self) -> None:
        self.table.register_loop_variable("i")
        with self.assertRaises(st.NameConflictError) as ctx:
            self.table.register_scalar("i", line=6)
        self.assertEqual(ctx.exception.line, 6)
        self.table.register_scalar("s")
        with self.assertRaises(st.NameConflictError) as ctx:
            self.table.declare_array(*small("s", 0), line=3)
        self.assertEqual(ctx.exception.line, 3)

    def test_array_scalar_conflict_is_reported_before_everything_else(self) -> None:
        # S3 is the lowest-numbered rule: it wins over S5 (design decision 44).
        self.table.register_scalar("A")
        with self.assertRaises(st.NameConflictError) as ctx:
            self.table.declare_array("A", [(5, 4)], 0)
        self.assertEqual(str(ctx.exception), self.S3)

    def test_bad_bounds_are_reported_before_the_loop_variable_conflict(self) -> None:
        # S5 comes before S10.
        self.table.register_loop_variable("i")
        with self.assertRaises(st.DeclarationError) as ctx:
            self.table.declare_array("i", [(5, 4)], 0)
        self.assertNotIsInstance(ctx.exception, st.NameConflictError)

    def test_no_conflict_for_distinct_names(self) -> None:
        self.table.declare_array(*small("A", 0))
        self.table.register_scalar("s")
        self.table.register_loop_variable("i")
        self.table.register_scalar("a")  # different case
        self.assertEqual(
            [self.table.kind_of(n) for n in ("A", "s", "i", "a", "zzz")],
            [st.KIND_ARRAY, st.KIND_SCALAR, st.KIND_LOOP_VARIABLE, st.KIND_SCALAR, None],
        )

    def test_scoping_rules_are_left_to_phase_3(self) -> None:
        # S9 (nested reuse) and S11 (use outside the loop) need the AST; the
        # table only knows that "i" is a loop variable, however often it is registered.
        self.table.register_loop_variable("i")
        self.table.register_loop_variable("i")
        self.assertEqual(self.table.loop_variables, ("i",))

    def test_matrix_multiply_program_names(self) -> None:
        # The names of examples/matrix_multiply.ml register without conflicts.
        t = self.table
        for name, base in (("X", 0), ("Y", 16), ("Z", 32)):
            t.declare_array(name, [(1, 4), (1, 4)], base)
        for v in ("i", "j", "k"):
            t.register_loop_variable(v)
        self.assertEqual([a.name for a in t.arrays], ["X", "Y", "Z"])
        self.assertEqual(t.loop_variables, ("i", "j", "k"))
        self.assertEqual(t.scalars, ())

    def test_one_d_sum_program_names(self) -> None:
        # examples/1d_sum.ml: array V, scalar s, loop variable i used by two loops.
        t = self.table
        t.declare_array("V", [(5, 14)], 500)
        t.register_loop_variable("i")
        t.register_scalar("s")
        t.register_scalar("s")
        t.register_loop_variable("i")
        self.assertEqual((t.scalars, t.loop_variables), (("s",), ("i",)))


HEADER = "== Array information vector " + "=" * 46  # 28 + 46 = 74 characters

EXPECTED_A = "\n".join(
    [
        HEADER,
        " array A   n = 2   type = int   a = 1000",
        "  dim  l   u   d",
        "   1   1   10  10     stride 20",
        "   2   1   20  20     stride 1",
        " C = 21    CONSPART = a - C = 979    size = 200",
    ]
)

EXPECTED_V5 = "\n".join(
    [
        HEADER,
        " array V   n = 1   type = int   a = 500",
        "  dim  l   u   d",
        "   1   5   14  10     stride 1",
        " C = 5    CONSPART = a - C = 495    size = 10",
    ]
)

EXPECTED_N = "\n".join(
    [
        HEADER,
        " array N   n = 2   type = int   a = 200",
        "  dim  l   u   d",
        "   1   -5  5   11     stride 7",
        "   2   -3  3   7      stride 1",
        " C = -38    CONSPART = a - C = 238    size = 77",
    ]
)

EXPECTED_W = "\n".join(  # negative bounds and a negative base address
    [
        HEADER,
        " array W   n = 2   type = int   a = -50",
        "  dim  l   u   d",
        "   1   -2  1   4      stride 3",
        "   2   0   2   3      stride 1",
        " C = -6    CONSPART = a - C = -44    size = 12",
    ]
)


class InformationVectorPrinterTests(unittest.TestCase):
    def test_header_is_74_wide(self) -> None:
        self.assertEqual(len(HEADER), 74)
        self.assertEqual(st.REPORT_WIDTH, 74)

    def test_roadmap_a_array_exact_output(self) -> None:
        info = st.ArrayInfo("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(st.format_array_info(info), EXPECTED_A)
        table = st.SymbolTable()
        table.declare_array("A", [(1, 10), (1, 20)], 1000)
        self.assertEqual(table.format_information_vectors(), EXPECTED_A)
        self.assertEqual(st.format_information_vectors([info]), EXPECTED_A)

    def test_matches_the_sample_in_quad_spec_section_9_1(self) -> None:
        import re

        path = pathlib.Path(__file__).resolve().parent.parent / "docs" / "quad_spec.md"
        if not path.exists():
            self.skipTest("docs/quad_spec.md not found")
        match = re.search(r"### 9\.1.*?```\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(match, "no code block under 9.1")
        self.assertEqual(match.group(1).rstrip("\n"), EXPECTED_A)

    def test_one_dimensional_array(self) -> None:
        self.assertEqual(
            st.format_array_info(st.ArrayInfo("V", [(5, 14)], 500)), EXPECTED_V5
        )

    def test_negative_bounds(self) -> None:
        self.assertEqual(
            st.format_array_info(st.ArrayInfo("N", [(-5, 5), (-3, 3)], 200)), EXPECTED_N
        )

    def test_negative_bounds_and_negative_base_address(self) -> None:
        self.assertEqual(
            st.format_array_info(st.ArrayInfo("W", [(-2, 1), (0, 2)], -50)), EXPECTED_W
        )

    def test_all_fields_appear(self) -> None:
        info = st.ArrayInfo("M", [(0, 3), (0, 4), (0, 5)], 0)
        text = st.format_array_info(info)
        for needle in (
            "array M", "n = 3", "type = int", "a = 0", "C = 0", "CONSPART = a - C = 0",
            "size = 120", "stride 30", "stride 6", "stride 1",
        ):
            self.assertIn(needle, text)
        rows = text.splitlines()[3:6]
        self.assertEqual(
            rows,
            ["   1   0   3   4      stride 30",
             "   2   0   4   5      stride 6",
             "   3   0   5   6      stride 1"],
        )

    def test_four_dimensional_array(self) -> None:
        text = st.format_array_info(st.ArrayInfo("H", [(0, 1), (0, 1), (0, 1), (0, 2)], 0))
        self.assertEqual(
            text.splitlines()[3:7],
            ["   1   0   1   2      stride 12",
             "   2   0   1   2      stride 6",
             "   3   0   1   2      stride 3",
             "   4   0   2   3      stride 1"],
        )
        self.assertEqual(text.splitlines()[-1], " C = 0    CONSPART = a - C = 0    size = 24")

    def test_size_one_dimension(self) -> None:
        text = st.format_array_info(st.ArrayInfo("U", [(1, 1), (1, 5)], 50))
        self.assertEqual(
            text.splitlines()[3:5],
            ["   1   1   1   1      stride 5", "   2   1   5   5      stride 1"],
        )

    def test_multiple_arrays_in_declaration_order(self) -> None:
        table = st.SymbolTable()
        table.declare_array("Z", [(1, 4), (1, 4)], 32)
        table.declare_array("X", [(1, 4), (1, 4)], 0)
        table.declare_array("Y", [(1, 4), (1, 4)], 16)
        text = table.format_information_vectors()
        blocks = text.split("\n\n")
        self.assertEqual(len(blocks), 3)
        self.assertEqual([b.splitlines()[1] for b in blocks],
                         [f" array {n}   n = 2   type = int   a = {a}"
                          for n, a in (("Z", 32), ("X", 0), ("Y", 16))])
        for block in blocks:
            self.assertEqual(block.splitlines()[0], HEADER)  # one full block per array
        self.assertEqual(blocks, [st.format_array_info(a) for a in table.arrays])

    def test_order_follows_declaration_not_name_or_address(self) -> None:
        for order in itertools.permutations(["A", "B", "C"]):
            with self.subTest(order=order):
                table = st.SymbolTable()
                for name in order:
                    table.declare_array(name, [(0, 9)], {"A": 300, "B": 100, "C": 200}[name])
                text = table.format_information_vectors()
                self.assertEqual(
                    [ln.split()[1] for ln in text.splitlines() if ln.startswith(" array ")],
                    list(order),
                )

    def test_blocks_are_separated_by_one_blank_line_and_no_trailing_newline(self) -> None:
        table = st.SymbolTable()
        table.declare_array("A", [(1, 10), (1, 20)], 1000)
        table.declare_array("V", [(5, 14)], 500)
        self.assertEqual(table.format_information_vectors(), EXPECTED_A + "\n\n" + EXPECTED_V5)
        self.assertFalse(table.format_information_vectors().endswith("\n"))

    def test_empty_symbol_table(self) -> None:
        expected = HEADER + "\n (no arrays declared)"
        self.assertEqual(st.SymbolTable().format_information_vectors(), expected)
        self.assertEqual(st.format_information_vectors([]), expected)
        self.assertEqual(st.format_information_vectors(iter(())), expected)

    def test_table_with_only_scalars_and_loop_variables_counts_as_empty(self) -> None:
        table = st.SymbolTable()
        table.register_scalar("s")
        table.register_loop_variable("i")
        self.assertEqual(
            table.format_information_vectors(), HEADER + "\n (no arrays declared)"
        )

    def test_scalars_and_loop_variables_are_not_printed(self) -> None:
        table = st.SymbolTable()
        table.register_scalar("s")
        table.declare_array("A", [(1, 10), (1, 20)], 1000)
        table.register_loop_variable("i")
        self.assertEqual(table.format_information_vectors(), EXPECTED_A)

    def test_deterministic(self) -> None:
        table = st.SymbolTable()
        for name, base, lows, ups, *_ in ROADMAP_ARRAYS[:2] + ROADMAP_ARRAYS[2:3]:
            table.declare_array(name, bounds_of(lows, ups), base)
        first = table.format_information_vectors()
        self.assertEqual(first, table.format_information_vectors())
        again = st.SymbolTable()
        for info in table.arrays:
            again.declare_array(info.name, bounds_of(info.lowers, info.uppers), info.base)
        self.assertEqual(first, again.format_information_vectors())

    def test_printing_does_not_change_the_table(self) -> None:
        table = st.SymbolTable()
        table.declare_array("A", [(1, 10), (1, 20)], 1000)
        before = (table.arrays, table.scalars, table.loop_variables)
        table.format_information_vectors()
        self.assertEqual((table.arrays, table.scalars, table.loop_variables), before)
        table.declare_array("B", [(0, 4)], 2000)  # registration still works afterwards
        self.assertEqual(len(table.arrays), 2)

    def test_output_hygiene(self) -> None:
        # Records are formatted individually: the sample arrays overlap each other.
        for name, base, lows, ups, *_ in ROADMAP_ARRAYS + EXTRA_ARRAYS:
            text = st.format_array_info(st.ArrayInfo(name, bounds_of(lows, ups), base))
            with self.subTest(array=name):
                self.assertNotIn("\r", text)
                self.assertNotIn("\t", text)
                self.assertTrue(text.isascii())
                for line in text.splitlines():
                    self.assertEqual(line, line.rstrip(), "trailing whitespace")
                self.assertEqual(len(text.splitlines()[0]), 74)

    def test_columns_line_up_for_every_sample_array(self) -> None:
        import re

        row = re.compile(r"^ {0,3}(\d) {3}(-?\d+) +(-?\d+) +(\d+) +stride (\d+)$")
        for name, base, lows, ups, d, _, _, stride, _ in ROADMAP_ARRAYS + EXTRA_ARRAYS:
            with self.subTest(array=name):
                lines = st.format_array_info(
                    st.ArrayInfo(name, bounds_of(lows, ups), base)
                ).splitlines()
                header, rows = lines[2], lines[3:-1]
                self.assertEqual(len(rows), len(lows))
                for k, line in enumerate(rows):
                    m = row.match(line)
                    self.assertIsNotNone(m, line)
                    self.assertEqual(int(m.group(1)), k + 1)
                    self.assertEqual(
                        (int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))),
                        (lows[k], ups[k], d[k], stride[k]),
                    )
                    # each value starts exactly under its column title
                    self.assertEqual(m.start(2), header.index("l"))
                    self.assertEqual(m.start(3), header.index("u"))
                    self.assertEqual(m.start(4), header.rindex("d"))
                self.assertEqual(len({r.index("stride") for r in rows}), 1)

    def test_wide_values_widen_the_columns_and_stay_aligned(self) -> None:
        import re

        info = st.ArrayInfo("BIG", [(-1000, 1000), (0, 99999), (-5, 123456)], -7)
        lines = st.format_array_info(info).splitlines()
        header, rows = lines[2], lines[3:-1]
        pat = re.compile(r"^ +\d +(-?\d+) +(-?\d+) +(\d+) +stride (\d+)$")
        for k, line in enumerate(rows):
            m = pat.match(line)
            self.assertIsNotNone(m, line)
            self.assertEqual(
                (m.start(1), m.start(2), m.start(3)),
                (header.index("l"), header.index("u"), header.rindex("d")),
            )
            self.assertEqual(int(m.group(3)), info.sizes[k])
        self.assertEqual(len({r.index("stride") for r in rows}), 1)
        # values never touch the next column
        for line in rows:
            self.assertIn("  ", line.strip())
        self.assertEqual(
            lines[-1],
            f" C = {info.c}    CONSPART = a - C = {info.conspart}    size = {info.size}",
        )

    def test_long_names_do_not_disturb_the_table_columns(self) -> None:
        text = st.format_array_info(st.ArrayInfo("matrix_with_a_long_name", [(1, 10), (1, 20)], 1000))
        self.assertEqual(text.splitlines()[1], " array matrix_with_a_long_name   n = 2   type = int   a = 1000")
        self.assertEqual(text.splitlines()[3:5], EXPECTED_A.splitlines()[3:5])


class SymbolTableIndependenceTests(unittest.TestCase):
    """Roadmap Phase 2: the symbol table must not depend on parser code."""

    def test_imports(self) -> None:
        tree = ast.parse(pathlib.Path(st.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                # "from . import address_math" has module None and level 1.
                names = [node.module] if node.module else [a.name for a in node.names]
                imported |= set(names)
        self.assertEqual(
            {m.split(".")[0] for m in imported} - {"collections", "dataclasses", "typing"},
            {"address_math"},
        )

    def test_importing_does_not_load_front_end_modules(self) -> None:
        # A fresh interpreter, so that other test modules cannot affect the result.
        import subprocess
        import sys

        root = str(pathlib.Path(st.__file__).resolve().parent.parent)
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import arraypeep.symbol_table;"
            "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))"
        )
        out = subprocess.run(
            [sys.executable, "-I", "-c", code, root],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(out, "['arraypeep.address_math', 'arraypeep.symbol_table']")


if __name__ == "__main__":
    unittest.main()
