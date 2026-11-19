"""Tests for arraypeep.semantic (roadmap Phase 3, task 3.6; rules S1 to S12 of language_spec.md Section 5)."""
import pathlib
import unittest

from arraypeep.parser import parse
from arraypeep.semantic import SemanticError, analyze
from arraypeep.symbol_table import ArrayPeepError, SymbolTable

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))

DECL_A = "array A[1:10, 1:20] at 1000;\n"


def check(source: str) -> SymbolTable:
    return analyze(parse(source))


def error_of(source: str) -> SemanticError:
    with unittest.TestCase().assertRaises(SemanticError) as ctx:
        check(source)
    return ctx.exception


class SemanticTestCase(unittest.TestCase):
    def assertError(self, source: str, line: int, message: str) -> None:
        err = error_of(source)
        self.assertEqual((err.line, err.message), (line, message), source)


class ValidProgramTests(unittest.TestCase):
    def test_empty_program(self) -> None:
        table = check("")
        self.assertEqual((table.arrays, table.scalars, table.loop_variables), ((), (), ()))

    def test_all_examples_pass(self) -> None:
        self.assertEqual(len(EXAMPLES), 5)
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                self.assertIsInstance(check(path.read_text(encoding="utf-8")), SymbolTable)

    def test_symbol_table_of_an_example(self) -> None:
        table = check((ROOT / "examples" / "matrix_multiply.ml").read_text(encoding="utf-8"))
        self.assertEqual([a.name for a in table.arrays], ["X", "Y", "Z"])
        self.assertEqual([a.conspart for a in table.arrays], [-5, 11, 27])  # roadmap T11
        self.assertEqual(table.loop_variables, ("i", "j", "k"))
        self.assertEqual(table.scalars, ())

    def test_symbol_table_of_1d_sum(self) -> None:
        table = check((ROOT / "examples" / "1d_sum.ml").read_text(encoding="utf-8"))
        self.assertEqual(table.get_array("V").conspart, 495)
        self.assertEqual((table.scalars, table.loop_variables), (("s",), ("i",)))

    def test_scalars_come_into_existence_at_first_mention(self) -> None:
        table = check("x = y + 1;\nz = x;")
        self.assertEqual(table.scalars, ("x", "y", "z"))  # y is read before it is assigned: legal, 0

    def test_sequential_loops_may_reuse_a_variable(self) -> None:
        table = check("for i = 1 to 2 do end\nfor i = 1 to 3 do end\nfor j = 1 to 2 do for i = 1 to 2 do end end")
        self.assertEqual(table.loop_variables, ("i", "j"))

    def test_loop_variable_read_inside_its_own_and_inner_loops(self) -> None:
        check("for i = 1 to 3 do\n for j = 1 to 3 do\n  s = i + j;\n end\n s = i;\nend")

    def test_array_element_assignments_and_uses(self) -> None:
        check(DECL_A + "for i = 1 to 4 do\n for j = 2 to 20 do\n  A[i, j] = A[2*i + 1, j - 1] + 1;\n end\nend")

    def test_non_linear_subscripts_are_semantically_fine(self) -> None:
        check("array V[0:99] at 0;\narray W[0:9] at 100;\nfor i = 0 to 9 do\n for j = 0 to 9 do\n"
              "  s = V[i * j] + V[W[i]];\n end\nend")

    def test_zero_trip_loop_is_legal(self) -> None:
        check("for i = 5 to 1 do s = s + 1; end")

    def test_adjacent_arrays(self) -> None:
        self.assertEqual(len(check("array A[1:10] at 0;\narray B[1:10] at 10;").arrays), 2)

    def test_declaration_after_a_loop_that_does_not_use_it(self) -> None:
        check("for i = 1 to 2 do s = s + i; end\narray A[1:2] at 0;\nA[1] = s;")

    def test_names_that_look_like_generated_names_but_are_not(self) -> None:
        check("T = 1;\nP = 2;\nT1a = 3;\nTx = 4;\nt1 = 5;\np2 = 6;")

    def test_analysis_does_not_change_the_tree(self) -> None:
        prog = parse(DECL_A + "A[1, 1] = 2;")
        before = repr(prog)
        analyze(prog)
        self.assertEqual(repr(prog), before)


class S1UndeclaredArrayTests(SemanticTestCase):
    def test_roadmap_example(self) -> None:
        self.assertError("s = 0;\nQ[1] = 2;", 2, "array 'Q' is not declared")

    def test_in_an_expression(self) -> None:
        self.assertError("s = Q[1];", 1, "array 'Q' is not declared")
        self.assertError("array A[1:2] at 0;\ns = A[1] + Q[2];", 2, "array 'Q' is not declared")

    def test_in_a_subscript(self) -> None:
        self.assertError("array A[1:2] at 0;\ns = A[Q[1]];", 2, "array 'Q' is not declared")

    def test_declared_later_is_still_undeclared(self) -> None:
        self.assertError("s = A[1];\narray A[1:2] at 0;", 1, "array 'A' is not declared")
        self.assertError("A[1] = 0;\narray A[1:2] at 0;", 1, "array 'A' is not declared")

    def test_inside_a_loop(self) -> None:
        self.assertError("for i = 1 to 2 do\n  B[i] = 1;\nend", 2, "array 'B' is not declared")

    def test_a_scalar_name_is_not_an_array(self) -> None:
        self.assertError("x = 1;\ny = x[1];", 2, "array 'x' is not declared")

    def test_a_loop_variable_name_is_not_an_array(self) -> None:
        self.assertError("for i = 1 to 2 do\n  s = i[1];\nend", 2, "array 'i' is not declared")


class S2SubscriptCountTests(SemanticTestCase):
    def test_roadmap_example(self) -> None:
        self.assertError(DECL_A + "for i = 1 to 2 do\n  s = A[i];\nend", 3,
                         "array 'A' has 2 dimensions but 1 subscript(s) given")

    def test_too_many_subscripts(self) -> None:
        self.assertError("array V[0:9] at 0;\ns = V[1, 2];", 2,
                         "array 'V' has 1 dimensions but 2 subscript(s) given")
        self.assertError("array M[0:3,0:4,0:5] at 0;\ns = M[1, 2, 3, 4];", 2,
                         "array 'M' has 3 dimensions but 4 subscript(s) given")

    def test_on_the_assignment_target(self) -> None:
        self.assertError(DECL_A + "A[1] = 0;", 2, "array 'A' has 2 dimensions but 1 subscript(s) given")

    def test_in_a_nested_subscript(self) -> None:
        self.assertError("array V[0:9] at 0;\narray A[1:2,1:2] at 100;\ns = A[V[1, 2], 1];", 3,
                         "array 'V' has 1 dimensions but 2 subscript(s) given")

    def test_correct_counts_for_one_to_four_dimensions(self) -> None:
        check("array D1[0:1] at 0;\narray D2[0:1,0:1] at 10;\narray D3[0:1,0:1,0:1] at 20;\n"
              "array D4[0:1,0:1,0:1,0:1] at 30;\ns = D1[0] + D2[0,0] + D3[0,0,0] + D4[0,0,0,0];")


class S3ArrayWithoutSubscriptsTests(SemanticTestCase):
    def test_in_an_expression(self) -> None:
        self.assertError(DECL_A + "s = A + 1;", 2, "array 'A' used without subscripts")

    def test_as_an_assignment_target(self) -> None:
        self.assertError(DECL_A + "A = 1;", 2, "array 'A' used without subscripts")

    def test_before_the_declaration(self) -> None:
        # An array name is an array everywhere (design decision 44).
        self.assertError("A = 1;\narray A[1:2] at 0;", 1, "array 'A' used without subscripts")
        self.assertError("s = A;\narray A[1:2] at 0;", 1, "array 'A' used without subscripts")

    def test_as_a_subscript(self) -> None:
        self.assertError("array A[1:2] at 0;\narray B[1:2] at 10;\ns = B[A];", 3,
                         "array 'A' used without subscripts")

    def test_inside_a_loop(self) -> None:
        self.assertError(DECL_A + "for i = 1 to 2 do\n  s = s + A;\nend", 3,
                         "array 'A' used without subscripts")


class S4DuplicateArrayTests(SemanticTestCase):
    def test_roadmap_example(self) -> None:
        self.assertError("array A[1:2] at 0;\narray A[1:2] at 100;", 2, "array 'A' is already declared")

    def test_with_a_different_shape(self) -> None:
        self.assertError("array A[1:2] at 0;\narray A[0:9, 0:9] at 500;", 2, "array 'A' is already declared")


class S5S6S7DeclarationTests(SemanticTestCase):
    def test_s5_upper_below_lower(self) -> None:
        self.assertError("array B[5:4] at 0;", 1,
                         "upper bound 4 is less than lower bound 5 in dimension 1 of array 'B'")
        self.assertError("s = 0;\narray B[1:3, 0:-1] at 0;", 2,
                         "upper bound -1 is less than lower bound 0 in dimension 2 of array 'B'")

    def test_s6_more_than_four_dimensions(self) -> None:
        self.assertError("array B[0:1,0:1,0:1,0:1,0:1] at 0;", 1,
                         "array 'B' has 5 dimensions; at most 4 are supported")

    def test_s5_is_reported_before_s6(self) -> None:
        self.assertError("array B[2:1,0:1,0:1,0:1,0:1] at 0;", 1,
                         "upper bound 1 is less than lower bound 2 in dimension 1 of array 'B'")

    def test_s7_overlap(self) -> None:
        self.assertError(DECL_A + "array B[1:20] at 1100;", 2,
                         "array 'B' (addresses 1100..1119) overlaps array 'A' (addresses 1000..1199)")

    def test_s7_partial_and_identical_and_one_cell(self) -> None:
        self.assertError("array A[0:9] at 0;\narray B[0:9] at 5;", 2,
                         "array 'B' (addresses 5..14) overlaps array 'A' (addresses 0..9)")
        self.assertError("array A[0:9] at 0;\narray B[0:9] at 0;", 2,
                         "array 'B' (addresses 0..9) overlaps array 'A' (addresses 0..9)")
        self.assertError("array A[0:9] at 0;\narray B[0:3] at 9;", 2,
                         "array 'B' (addresses 9..12) overlaps array 'A' (addresses 0..9)")

    def test_s7_with_negative_addresses(self) -> None:
        self.assertError("array A[0:9] at -10;\narray B[0:9] at -5;", 2,
                         "array 'B' (addresses -5..4) overlaps array 'A' (addresses -10..-1)")

    def test_s4_before_s7(self) -> None:
        self.assertError("array A[0:9] at 0;\narray A[0:9] at 0;", 2, "array 'A' is already declared")

    def test_roadmap_sample_arrays_overlap_in_one_program(self) -> None:
        # M@0 (0..119) overlaps V0@100 (100..109): the roadmap's samples are separate programs.
        self.assertError("array V[0:9] at 100;\narray M[0:3,0:4,0:5] at 0;", 2,
                         "array 'M' (addresses 0..119) overlaps array 'V' (addresses 100..109)")


class S8AssignedLoopVariableTests(SemanticTestCase):
    def test_roadmap_example(self) -> None:
        self.assertError("for i = 1 to 3 do\n  i = 5;\nend", 2, "loop variable 'i' cannot be assigned")

    def test_in_a_nested_loop(self) -> None:
        self.assertError("for i = 1 to 2 do\n for j = 1 to 2 do\n  i = 0;\n end\nend", 3,
                         "loop variable 'i' cannot be assigned")

    def test_outside_any_loop_of_that_name(self) -> None:
        # S8 has the lower number, so it wins over S10 (design decision 44).
        self.assertError("i = 0;\nfor i = 1 to 2 do end", 1, "loop variable 'i' cannot be assigned")
        self.assertError("for i = 1 to 2 do end\ni = 0;", 2, "loop variable 'i' cannot be assigned")

    def test_array_element_target_with_a_loop_variable_subscript_is_fine(self) -> None:
        check("array A[1:3] at 0;\nfor i = 1 to 3 do\n  A[i] = i;\nend")


class S9NestedReuseTests(SemanticTestCase):
    def test_roadmap_example(self) -> None:
        self.assertError("for i = 1 to 2 do\n  for i = 1 to 2 do end\nend", 2,
                         "loop variable 'i' is already used by an enclosing loop")

    def test_three_levels(self) -> None:
        self.assertError("for i = 1 to 2 do\n for j = 1 to 2 do\n  for i = 1 to 2 do end\n end\nend", 3,
                         "loop variable 'i' is already used by an enclosing loop")

    def test_sibling_loops_are_fine(self) -> None:
        check("for i = 1 to 2 do\n for j = 1 to 2 do end\n for j = 1 to 3 do end\nend")


class S10NameConflictTests(SemanticTestCase):
    S10 = "'i' is used as a loop variable and as an ordinary name"

    def test_loop_variable_named_like_an_array(self) -> None:
        self.assertError("array i[1:2] at 0;\nfor i = 1 to 2 do end", 1, self.S10)  # at the first offender
        self.assertError("for i = 1 to 2 do end\narray i[1:2] at 0;", 1, self.S10)

    def test_array_declared_after_a_loop_with_that_name_elsewhere(self) -> None:
        self.assertError("for j = 1 to 2 do end\narray i[1:2] at 0;\nfor i = 1 to 2 do end", 2, self.S10)

    def test_read_outside_the_loop(self) -> None:
        self.assertError("for i = 1 to 2 do end\ns = i;", 2, self.S10)
        self.assertError("s = i;\nfor i = 1 to 2 do end", 1, self.S10)

    def test_s11_is_subsumed_by_s10(self) -> None:
        # S10 < S11, so a loop variable read outside its loop reports S10 (design decision 44).
        err = error_of("for i = 1 to 2 do end\ns = i;")
        self.assertIn("is used as a loop variable and as an ordinary name", err.message)
        self.assertNotIn("outside its loop", err.message)

    def test_read_of_an_inner_variable_after_its_loop(self) -> None:
        self.assertError("for i = 1 to 2 do\n for j = 1 to 2 do end\n s = j;\nend", 3,
                         "'j' is used as a loop variable and as an ordinary name")

    def test_read_of_a_loop_variable_in_a_sibling_loop(self) -> None:
        self.assertError("for i = 1 to 2 do end\nfor j = 1 to 2 do\n  s = i;\nend", 3, self.S10)

    def test_in_a_subscript_outside_the_loop(self) -> None:
        self.assertError("array A[1:3] at 0;\nfor i = 1 to 3 do end\ns = A[i];", 3, self.S10)


class S12ReservedNameTests(SemanticTestCase):
    def test_scalar_names(self) -> None:
        self.assertError("T1 = 0;", 1, "'T1' is reserved for compiler temporaries")
        self.assertError("s = 1;\nP12 = s;", 2, "'P12' is reserved for compiler temporaries")
        self.assertError("x = T3 + 1;", 1, "'T3' is reserved for compiler temporaries")
        self.assertError("T01 = 1;", 1, "'T01' is reserved for compiler temporaries")

    def test_array_and_loop_variable_names(self) -> None:
        self.assertError("array T2[1:2] at 0;", 1, "'T2' is reserved for compiler temporaries")
        self.assertError("for P3 = 1 to 2 do end", 1, "'P3' is reserved for compiler temporaries")

    def test_s7_is_reported_before_s12(self) -> None:
        self.assertError("array A[1:10] at 0;\narray T1[1:10] at 5;", 2,
                         "array 'T1' (addresses 5..14) overlaps array 'A' (addresses 0..9)")


class OrderAndLineTests(SemanticTestCase):
    def test_first_error_in_source_order(self) -> None:
        self.assertError("s = Q[1];\nt = R[1];", 1, "array 'Q' is not declared")
        self.assertError("array A[1:2] at 0;\nx = A[1,2];\ny = B[1];", 2,
                         "array 'A' has 1 dimensions but 2 subscript(s) given")

    def test_target_is_checked_before_the_value(self) -> None:
        self.assertError("array A[1:2] at 0;\nA[1,1] = B[1];", 2,
                         "array 'A' has 1 dimensions but 2 subscript(s) given")

    def test_only_one_error_is_reported(self) -> None:
        err = error_of("s = Q[1] + R[2];\nT1 = 3;")
        self.assertEqual(err.message, "array 'Q' is not declared")

    def test_line_of_a_reference_inside_a_multi_line_statement(self) -> None:
        # the offending construct is the reference; the line is where it starts
        self.assertError("array A[1:2] at 0;\ns = 1 +\n   2 +\n   A[1, 1];", 4,
                         "array 'A' has 1 dimensions but 2 subscript(s) given")
        self.assertError("s = 1 +\n  Q[1];", 2, "array 'Q' is not declared")

    def test_line_of_a_multi_line_assignment_target(self) -> None:
        self.assertError("i = 0;\nfor i = 1 to 2 do end", 1, "loop variable 'i' cannot be assigned")

    def test_s3_before_s8_before_s12(self) -> None:
        self.assertError("array T1[1:2] at 0;", 1, "'T1' is reserved for compiler temporaries")
        self.assertError("T1 = 1;\nfor T1 = 1 to 2 do end", 1, "loop variable 'T1' cannot be assigned")

    def test_errors_inside_nested_loops_report_the_inner_line(self) -> None:
        self.assertError("for i = 1 to 2 do\n for j = 1 to 2 do\n  s = Q[j];\n end\nend", 3,
                         "array 'Q' is not declared")


class ErrorObjectTests(unittest.TestCase):
    def test_formatting(self) -> None:
        err = error_of("s = 0;\nt = 1;\nu = 2;\nv = 3;\nw = 4;\nQ[1] = 2;")
        self.assertEqual(err.format("prog.ml"),
                         "prog.ml: line 6: semantic error: array 'Q' is not declared")

    def test_common_base_class_and_str(self) -> None:
        err = error_of("Q[1] = 2;")
        self.assertIsInstance(err, ArrayPeepError)
        self.assertEqual(str(err), "array 'Q' is not declared")
        self.assertEqual(err.line, 1)


if __name__ == "__main__":
    unittest.main()
