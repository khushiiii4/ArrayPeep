"""Tests for tests/oracle.py (Phase 11.1: the independent oracle).

Every expected value below is hand-calculated from Eq. 1 (docs/hand_examples.md
and the roadmap's Section 5.3); none comes from the compiler. The tests do not
import ``arraypeep`` either.
"""
import ast
import pathlib
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:  # also works for `python -m unittest tests.test_oracle`
    sys.path.insert(0, str(TESTS_DIR))

import oracle  # noqa: E402
from oracle import Array, Loop, OracleError, Statement, assign, expected_trace, linear, read, write  # noqa: E402

A = Array("A", (1, 1), (10, 20), 1000)  # A[1:10, 1:20] at 1000
V5 = Array("V", (5,), (14,), 500)  # V[5:14] at 500
N = Array("N", (-5, -3), (5, 3), 200)  # N[-5:5, -3:3] at 200
M = Array("M", (0, 0, 0), (3, 4, 5), 0)  # M[0:3, 0:4, 0:5] at 0


def reads(addresses):
    return [("R", a) for a in addresses]


class Eq1Tests(unittest.TestCase):
    def test_roadmap_examples(self):
        self.assertEqual(A.address((2, 3)), 1022)
        self.assertEqual(V5.address((9,)), 504)
        self.assertEqual(N.address((-5, -3)), 200)

    def test_sizes_strides_and_size(self):
        self.assertEqual((A.sizes, A.strides, A.size), ((10, 20), (20, 1), 200))
        self.assertEqual((V5.sizes, V5.strides, V5.size), ((10,), (1,), 10))
        self.assertEqual((N.sizes, N.strides, N.size), ((11, 7), (7, 1), 77))
        self.assertEqual((M.sizes, M.strides, M.size), ((4, 5, 6), (30, 6, 1), 120))
        h = Array("H", (0, 0, 0, 0), (1, 1, 1, 2), 0)  # T17
        self.assertEqual((h.sizes, h.strides, h.size), ((2, 2, 2, 3), (12, 6, 3, 1), 24))

    def test_first_and_last_element(self):
        self.assertEqual((A.address((1, 1)), A.address((10, 20))), (1000, 1199))
        self.assertEqual((V5.address((5,)), V5.address((14,))), (500, 509))
        self.assertEqual((N.address((-5, -3)), N.address((5, 3))), (200, 276))
        self.assertEqual(N.address((0, 0)), 238)
        self.assertEqual((M.address((0, 0, 0)), M.address((3, 4, 5))), (0, 119))

    def test_addresses_cover_the_declared_region(self):
        self.assertEqual(A.addresses(), range(1000, 1200))
        self.assertEqual(N.addresses(), range(200, 277))

    def test_agrees_with_the_conspart_varpart_split(self):
        # Eq. 2, computed here by Horner's rule: a - C + VARPART
        def split(array, subscripts):
            c = v = 0
            for k, (l, u) in enumerate(zip(array.lowers, array.uppers)):
                d = u - l + 1
                c = c * d + l if k else l  # C = (...((l1*d2 + l2)*d3 + l3)...)*dn + ln
                v = v * d + subscripts[k] if k else subscripts[0]  # VARPART, same shape with the subscripts
            return array.base - c + v

        for array in (A, V5, N, M):
            ranges = [range(l, u + 1) for l, u in zip(array.lowers, array.uppers)]
            combos = [()]
            for r in ranges:
                combos = [c + (i,) for c in combos for i in r]
            for subscripts in combos:
                self.assertEqual(array.address(subscripts), split(array, subscripts))

    def test_ill_formed_arrays_and_accesses(self):
        with self.assertRaises(OracleError):
            Array("B", (1, 1), (5,), 0)
        with self.assertRaises(OracleError):
            Array("B", (5,), (4,), 0)
        for bad in ((0, 1), (11, 1), (1, 21), (1,), (1, 1, 1)):
            with self.subTest(subscripts=bad), self.assertRaises(OracleError):
                A.address(bad)


class InitialMemoryTests(unittest.TestCase):
    def test_init_values_from_the_spec(self):
        self.assertEqual([oracle.init_value(x) for x in (1000, 1001, 0, -1)], [737, 768, 7, 985])

    def test_hand_calculated_values(self):
        self.assertEqual(oracle.init_value(1), 38)  # 31 + 7
        self.assertEqual(oracle.init_value(33), 1030 % 1009)  # 31*33 + 7 = 1030
        self.assertEqual(oracle.init_value(33), 21)
        self.assertEqual(oracle.init_value(-2), (-62 + 7) % 1009)  # mathematical modulo, never negative
        self.assertEqual(oracle.init_value(-2), 954)

    def test_initial_memory_has_exactly_the_declared_cells(self):
        x = Array("X", (1, 1), (4, 4), 0)
        y = Array("Y", (1, 1), (4, 4), 16)
        memory = oracle.initial_memory([x, y])
        self.assertEqual(sorted(memory), list(range(32)))
        self.assertEqual(memory[0], 7)
        self.assertEqual(memory[16], (31 * 16 + 7) % 1009)
        self.assertTrue(all(0 <= v < 1009 for v in memory.values()))


class TraceTests(unittest.TestCase):
    def test_1d_sum(self):  # examples/1d_sum.ml: 10 writes, then 10 reads
        body = [
            Loop("i", 5, 14, [assign(write("V", "i"))]),
            Loop("i", 5, 14, [Statement([read("V", "i")])]),
        ]
        expected = [("W", a) for a in range(500, 510)] + reads(range(500, 510))
        self.assertEqual(expected_trace([V5], body), expected)

    def test_2d_traversal_t03(self):
        body = [Loop("i", 1, 10, [Loop("j", 1, 20, [Statement([read("A", "i", "j")])])])]
        self.assertEqual(expected_trace([A], body), reads(range(1000, 1200)))

    def test_t04_linear_subscripts(self):
        body = [Loop("i", 1, 4, [Loop("j", 2, 20, [Statement([read("A", linear(1, i=2), linear(-1, j=1))])])])]
        trace = expected_trace([A], body)
        self.assertEqual(len(trace), 76)
        self.assertEqual((trace[0], trace[-1]), (("R", 1040), ("R", 1178)))
        # per i the 19 addresses are consecutive: 1040..1058, 1080..1098, 1120..1138, 1160..1178
        expected = []
        for first in (1040, 1080, 1120, 1160):
            expected += reads(range(first, first + 19))
        self.assertEqual(trace, expected)

    def test_t01_zero_lower_bound(self):
        v0 = Array("V0", (0,), (9,), 100)
        body = [Loop("i", 0, 9, [Statement([read("V0", "i")])])]
        self.assertEqual(expected_trace([v0], body), reads(range(100, 110)))

    def test_t02_non_zero_lower_bound(self):
        body = [Loop("i", 5, 14, [Statement([read("V", "i")])])]
        self.assertEqual(expected_trace([V5], body), reads(range(500, 510)))

    def test_t05_three_dimensions(self):
        body = [Loop("i", 0, 3, [Loop("j", 0, 4, [Loop("k", 0, 5, [Statement([read("M", "i", "j", "k")])])])])]
        self.assertEqual(expected_trace([M], body), reads(range(0, 120)))

    def test_t06_negative_bounds(self):
        body = [Loop("i", -5, 5, [Loop("j", -3, 3, [Statement([read("N", "i", "j")])])])]
        trace = expected_trace([N], body)
        self.assertEqual((len(trace), trace[0], trace[-1]), (77, ("R", 200), ("R", 276)))
        self.assertEqual(trace, reads(range(200, 277)))

    def test_t07_constant_address_without_a_loop(self):
        self.assertEqual(expected_trace([A], [Statement([read("A", 3, 4)])]), [("R", 1043)])

    def test_t08_access_independent_of_the_inner_loop(self):
        body = [Loop("i", 1, 10, [Loop("j", 1, 2, [Statement([read("A", "i", 5)])])])]
        trace = expected_trace([A], body)
        self.assertEqual((trace[0], trace[-1]), (("R", 1004), ("R", 1184)))
        self.assertEqual(trace[:4], reads([1004, 1004, 1024, 1024]))
        self.assertEqual(len(trace), 20)

    def test_t09_transposed_access(self):
        s = Array("S", (1, 1), (8, 8), 300)
        body = [Loop("i", 1, 8, [Loop("j", 1, 8, [Statement([read("S", "j", "i")])])])]
        trace = expected_trace([s], body)
        self.assertEqual(trace[:3], reads([300, 308, 316]))
        self.assertEqual(trace[8], ("R", 301))  # i = 2, j = 1
        self.assertEqual(trace[-1], ("R", 363))
        self.assertEqual(sorted(a for _, a in trace), list(range(300, 364)))

    def test_t10_several_arrays(self):
        arrays = [Array("A", (1, 1), (4, 5), 0), Array("B", (1, 1), (4, 5), 100), Array("C", (1, 1), (4, 5), 200)]
        body = [Loop("i", 1, 4, [Loop("j", 1, 5, [assign(write("C", "i", "j"), read("A", "i", "j"), read("B", "i", "j"))])])]
        trace = expected_trace(arrays, body)
        self.assertEqual(len(trace), 60)
        self.assertEqual(trace[:6], [("R", 0), ("R", 100), ("W", 200), ("R", 1), ("R", 101), ("W", 201)])
        self.assertEqual(trace[-3:], [("R", 19), ("R", 119), ("W", 219)])

    def test_t11_matrix_multiply_order_and_count(self):  # examples/matrix_multiply.ml
        arrays = [Array("X", (1, 1), (4, 4), 0), Array("Y", (1, 1), (4, 4), 16), Array("Z", (1, 1), (4, 4), 32)]
        body = [Loop("i", 1, 4, [Loop("j", 1, 4, [
            assign(write("Z", "i", "j")),
            Loop("k", 1, 4, [assign(write("Z", "i", "j"), read("Z", "i", "j"), read("X", "i", "k"), read("Y", "k", "j"))]),
        ])])]
        trace = expected_trace(arrays, body)
        self.assertEqual(len(trace), 272)  # 16 clearing writes + 64 * 4 accesses
        # Z[1,1] = 0; then k = 1: R Z[1,1], R X[1,1], R Y[1,1], W Z[1,1]; k = 2: ... X[1,2] = 1, Y[2,1] = 20
        self.assertEqual(trace[:9], [("W", 32), ("R", 32), ("R", 0), ("R", 16), ("W", 32),
                                     ("R", 32), ("R", 1), ("R", 20), ("W", 32)])
        self.assertEqual(trace[-1], ("W", 47))

    def test_t12_non_linear_subscript_is_just_a_function(self):
        v = Array("V", (0,), (99,), 0)
        body = [Loop("i", 0, 9, [Loop("j", 0, 9, [Statement([read("V", lambda env: env["i"] * env["j"])])])])]
        trace = expected_trace([v], body)
        self.assertEqual(len(trace), 100)
        self.assertEqual(trace[:10], reads([0] * 10))  # i = 0
        self.assertEqual(trace[10:20], reads(range(10)))  # i = 1
        self.assertEqual(trace[-1], ("R", 81))

    def test_t15_zero_trip_loop(self):
        body = [Loop("i", 5, 1, [Statement([read("A", "i", 1)])])]
        self.assertEqual(expected_trace([A], body), [])

    def test_zero_trip_inner_loop_only(self):
        body = [Loop("i", 1, 3, [Loop("j", 2, 1, [Statement([read("A", "i", "j")])]), Statement([read("A", "i", 1)])])]
        self.assertEqual(expected_trace([A], body), reads([1000, 1020, 1040]))

    def test_t16_size_one_dimension_and_single_iteration(self):
        u = Array("U", (1, 1), (1, 5), 50)
        body = [Loop("i", 1, 1, [Loop("j", 1, 5, [Statement([read("U", "i", "j")])])])]
        self.assertEqual(expected_trace([u], body), reads(range(50, 55)))

    def test_t17_four_dimensions(self):
        h = Array("H", (0, 0, 0, 0), (1, 1, 1, 2), 0)
        body = [Loop("a", 0, 1, [Loop("b", 0, 1, [Loop("c", 0, 1, [Loop("d", 0, 2, [
            Statement([read("H", "a", "b", "c", "d")])])])])])]
        self.assertEqual(expected_trace([h], body), reads(range(24)))

    def test_t18_negative_coefficient(self):
        body = [Loop("i", 1, 10, [Loop("j", 1, 20, [Statement([read("A", linear(11, i=-1), "j")])])])]
        trace = expected_trace([A], body)
        self.assertEqual(len(trace), 200)
        self.assertEqual(trace[:2], reads([1180, 1181]))
        self.assertEqual(trace[20], ("R", 1160))  # i = 2: row 9
        self.assertEqual(trace[-1], ("R", 1019))  # i = 10: row 1, j = 20

    def test_zero_coefficient_equals_a_constant(self):
        body = [Loop("i", 1, 3, [Statement([read("A", linear(5, i=0), 2)])])]
        self.assertEqual(expected_trace([A], body), reads([1000 + 4 * 20 + 1] * 3))

    def test_same_element_twice_in_one_statement(self):
        body = [Loop("i", 5, 6, [assign(write("V", "i"), read("V", "i"), read("V", "i"))])]
        self.assertEqual(expected_trace([V5], body), [("R", 500), ("R", 500), ("W", 500), ("R", 501), ("R", 501), ("W", 501)])

    def test_assignment_order_is_reads_then_target_subscript_reads_then_store(self):
        # B[2] = A[i+1] with a further read A[i] standing for a read in the target's subscripts:
        # the RHS read comes first, then the target-subscript read, then the store
        a = Array("A", (1,), (4,), 0)
        b = Array("B", (1,), (4,), 10)
        stmt = assign(write("B", 2), read("A", linear(1, i=1)), read("A", "i"))
        self.assertEqual(expected_trace([a, b], [Loop("i", 1, 1, [stmt])]), [("R", 1), ("R", 0), ("W", 11)])

    def test_outer_variable_is_visible_in_inner_loop_and_sequential_loops_may_reuse_a_name(self):
        body = [
            Loop("i", 1, 2, [Loop("j", 1, 2, [Statement([read("A", "i", "j")])])]),
            Loop("i", 3, 3, [Statement([read("A", "i", 1)])]),
        ]
        self.assertEqual(expected_trace([A], body), reads([1000, 1001, 1020, 1021, 1040]))

    def test_constants_and_variable_names_as_subscripts(self):
        body = [Loop("i", 2, 2, [Statement([read("A", "i", 3), read("A", 2, 3)])])]
        self.assertEqual(expected_trace([A], body), reads([1022, 1022]))


class ErrorTests(unittest.TestCase):
    def check_error(self, arrays, body):
        with self.assertRaises(OracleError):
            expected_trace(arrays, body)

    def test_subscript_out_of_bounds(self):
        self.check_error([A], [Statement([read("A", 11, 1)])])
        self.check_error([A], [Loop("i", 1, 4, [Statement([read("A", linear(8, i=1), 1)])])])  # i = 3 gives 11

    def test_unknown_array(self):
        self.check_error([A], [Statement([read("B", 1, 1)])])

    def test_wrong_number_of_subscripts(self):
        self.check_error([A], [Statement([read("A", 1)])])

    def test_variable_not_in_scope(self):
        self.check_error([A], [Statement([read("A", "i", 1)])])
        self.check_error([A], [Statement([read("A", linear(0, i=1), 1)])])

    def test_loop_variable_reused_in_a_nest(self):
        self.check_error([A], [Loop("i", 1, 2, [Loop("i", 1, 2, [Statement([read("A", "i", 1)])])])])

    def test_duplicate_array_declaration(self):
        self.check_error([A, A], [])

    def test_assign_needs_a_write_target(self):
        with self.assertRaises(OracleError):
            assign(read("A", 1, 1))


class IndependenceTests(unittest.TestCase):
    """tests/oracle.py must not depend on the compiler (roadmap Section 4.3)."""

    ORACLE = TESTS_DIR / "oracle.py"

    @staticmethod
    def imported_modules(tree):
        modules = []
        for node in ast.walk(tree):  # includes imports inside functions and conditionals
            if isinstance(node, ast.Import):
                modules += [(alias.name, 0) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules.append((node.module or "", node.level))
        return modules

    def test_source_has_no_arraypeep_or_relative_import(self):
        tree = ast.parse(self.ORACLE.read_text(encoding="utf-8"))
        modules = self.imported_modules(tree)
        self.assertTrue(modules)  # the walk finds the standard-library imports
        for name, level in modules:
            self.assertEqual(level, 0, f"relative import of {name!r}")
            self.assertNotEqual(name.split(".")[0], "arraypeep", f"imports {name}")

    def test_only_standard_library_modules_are_imported(self):
        tree = ast.parse(self.ORACLE.read_text(encoding="utf-8"))
        for name, _ in self.imported_modules(tree):
            self.assertIn(name.split(".")[0], sys.stdlib_module_names, f"{name} is not in the standard library")

    def test_no_dynamic_imports(self):
        tree = ast.parse(self.ORACLE.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {"__import__", "importlib", "exec", "eval"})

    def test_importing_the_oracle_loads_no_compiler_module(self):
        # A fresh interpreter started away from the repository, so `arraypeep` is not even importable.
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import oracle; "
            "print(oracle.address if hasattr(oracle, 'address') else 'ok'); "
            "bad = [m for m in sys.modules if m == 'arraypeep' or m.startswith('arraypeep.')]; "
            "sys.exit(1 if bad else 0)"
        )
        with tempfile.TemporaryDirectory() as elsewhere:
            done = subprocess.run(
                [sys.executable, "-I", "-c", code, str(TESTS_DIR)],
                cwd=elsewhere, capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
