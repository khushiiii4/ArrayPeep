"""Tests for arraypeep.parser and the AST (roadmap Phase 3, tasks 3.3, 3.4 and 3.7)."""
import pathlib
import random
import subprocess
import sys
import unittest

from arraypeep import ast_nodes as ast
from arraypeep.lexer import MiniLoopSyntaxError
from arraypeep.parser import BASE_MESSAGE, BOUND_MESSAGE, LOOP_BOUND_MESSAGE, parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))


def num(v):
    return ast.Number(v)


def var(n):
    return ast.ScalarRef(n)


def bin_(op, a, b):
    return ast.BinOp(op, a, b)


def expr_of(text: str) -> ast.Expr:
    """Parse ``x = <text>;`` and return the right-hand side."""
    stmt = parse(f"x = {text};").items[0]
    assert isinstance(stmt, ast.Assign)
    return stmt.value


def syntax_error(source: str) -> MiniLoopSyntaxError:
    with unittest.TestCase().assertRaises(MiniLoopSyntaxError) as ctx:
        parse(source)
    return ctx.exception


class DeclarationTests(unittest.TestCase):
    def test_one_dimension(self) -> None:
        self.assertEqual(parse("array V[0:9] at 100;").items,
                         (ast.ArrayDecl("V", ((0, 9),), 100),))

    def test_two_dimensions(self) -> None:
        decl = parse("array A[1:10, 1:20] at 1000;").items[0]
        self.assertEqual((decl.name, decl.bounds, decl.base), ("A", ((1, 10), (1, 20)), 1000))

    def test_negative_bounds_and_base(self) -> None:
        decl = parse("array N[-5:5, -3:3] at -200;").items[0]
        self.assertEqual((decl.bounds, decl.base), (((-5, 5), (-3, 3)), -200))
        self.assertEqual(parse("array Q[-10:-1] at 0;").items[0].bounds, ((-10, -1),))

    def test_three_and_four_dimensions(self) -> None:
        self.assertEqual(parse("array M[0:3, 0:4, 0:5] at 0;").items[0].bounds,
                         ((0, 3), (0, 4), (0, 5)))
        self.assertEqual(len(parse("array H[0:1,0:1,0:1,0:2] at 0;").items[0].bounds), 4)

    def test_more_than_four_dimensions_parse(self) -> None:
        # The limit of four dimensions is semantic rule S6, not a syntax rule.
        self.assertEqual(len(parse("array B[0:1,0:1,0:1,0:1,0:1] at 0;").items[0].bounds), 5)

    def test_upper_below_lower_parses(self) -> None:
        self.assertEqual(parse("array B[5:4] at 0;").items[0].bounds, ((5, 4),))  # S5 is semantic

    def test_whitespace_and_layout_do_not_matter(self) -> None:
        a = parse("array A[1:10,1:20] at 1000;")
        b = parse("array\n  A [ 1 : 10 ,\n 1 : 20 ]\n at 1000 ;")
        self.assertEqual(a, b)

    def test_several_declarations(self) -> None:
        prog = parse("array X[1:4,1:4] at 0;\narray Y[1:4,1:4] at 16;\narray Z[1:4,1:4] at 32;")
        self.assertEqual([d.name for d in prog.items], ["X", "Y", "Z"])

    def test_declarations_and_statements_may_interleave(self) -> None:
        prog = parse("s = 0;\narray V[0:1] at 0;\nV[0] = s;")
        self.assertEqual([type(i) for i in prog.items], [ast.Assign, ast.ArrayDecl, ast.Assign])


class ExpressionTests(unittest.TestCase):
    def test_precedence_multiplication_above_addition(self) -> None:
        # Roadmap completion criterion: a + b * c and (a + b) * c give different trees.
        no_parens = expr_of("a + b * c")
        parens = expr_of("(a + b) * c")
        self.assertEqual(no_parens, bin_("+", var("a"), bin_("*", var("b"), var("c"))))
        self.assertEqual(parens, bin_("*", bin_("+", var("a"), var("b")), var("c")))
        self.assertNotEqual(no_parens, parens)

    def test_multiplication_first_on_either_side(self) -> None:
        self.assertEqual(expr_of("a * b + c"), bin_("+", bin_("*", var("a"), var("b")), var("c")))
        self.assertEqual(expr_of("a - b * c"), bin_("-", var("a"), bin_("*", var("b"), var("c"))))

    def test_left_associativity(self) -> None:
        self.assertEqual(expr_of("a - b - c"), bin_("-", bin_("-", var("a"), var("b")), var("c")))
        self.assertEqual(expr_of("a + b + c"), bin_("+", bin_("+", var("a"), var("b")), var("c")))
        self.assertEqual(expr_of("a * b * c"), bin_("*", bin_("*", var("a"), var("b")), var("c")))
        self.assertEqual(expr_of("a - b + c"), bin_("+", bin_("-", var("a"), var("b")), var("c")))
        self.assertEqual(expr_of("a - (b - c)"), bin_("-", var("a"), bin_("-", var("b"), var("c"))))

    def test_parentheses_do_not_leave_a_node(self) -> None:
        self.assertEqual(expr_of("((a))"), var("a"))
        self.assertEqual(expr_of("(1)"), num(1))

    def test_unary_minus(self) -> None:
        self.assertEqual(expr_of("-x"), ast.UnaryMinus(var("x")))
        self.assertEqual(expr_of("-5"), ast.UnaryMinus(num(5)))  # no constant folding
        self.assertEqual(expr_of("- -x"), ast.UnaryMinus(ast.UnaryMinus(var("x"))))
        self.assertEqual(expr_of("--x"), ast.UnaryMinus(ast.UnaryMinus(var("x"))))
        self.assertEqual(expr_of("-(a + b)"), ast.UnaryMinus(bin_("+", var("a"), var("b"))))

    def test_unary_minus_binds_tighter_than_multiplication(self) -> None:
        self.assertEqual(expr_of("-a * b"), bin_("*", ast.UnaryMinus(var("a")), var("b")))
        self.assertEqual(expr_of("a * -b"), bin_("*", var("a"), ast.UnaryMinus(var("b"))))

    def test_unary_and_binary_minus(self) -> None:
        self.assertEqual(expr_of("a - -b"), bin_("-", var("a"), ast.UnaryMinus(var("b"))))
        self.assertEqual(expr_of("-a - b"), bin_("-", ast.UnaryMinus(var("a")), var("b")))

    def test_literals_and_names(self) -> None:
        self.assertEqual(expr_of("42"), num(42))
        self.assertEqual(expr_of("s"), var("s"))

    def test_array_references_in_expressions(self) -> None:
        self.assertEqual(expr_of("A[i]"), ast.ArrayRef("A", (var("i"),)))
        self.assertEqual(expr_of("A[i, j]"), ast.ArrayRef("A", (var("i"), var("j"))))
        self.assertEqual(expr_of("M[i,j,k]"), ast.ArrayRef("M", (var("i"), var("j"), var("k"))))

    def test_subscripts_are_a_list_of_expressions(self) -> None:
        ref = expr_of("A[2*i + 1, j - 1]")
        self.assertIsInstance(ref, ast.ArrayRef)
        self.assertEqual(len(ref.subscripts), 2)
        self.assertEqual(ref.subscripts[0],
                         bin_("+", bin_("*", num(2), var("i")), num(1)))
        self.assertEqual(ref.subscripts[1], bin_("-", var("j"), num(1)))

    def test_any_expression_may_be_a_subscript(self) -> None:
        # Non-linear subscripts are legal MiniLoop (language_spec.md 4.4).
        self.assertEqual(expr_of("A[i * j, 1]").subscripts[0], bin_("*", var("i"), var("j")))
        self.assertEqual(expr_of("V[W[i]]"), ast.ArrayRef("V", (ast.ArrayRef("W", (var("i"),)),)))
        self.assertEqual(expr_of("V[k]").subscripts, (var("k"),))
        self.assertEqual(expr_of("A[-i, (j)]").subscripts,
                         (ast.UnaryMinus(var("i")), var("j")))

    def test_array_elements_in_arithmetic(self) -> None:
        self.assertEqual(
            expr_of("Z[i,j] + X[i,k] * Y[k,j]"),
            bin_("+", ast.ArrayRef("Z", (var("i"), var("j"))),
                 bin_("*", ast.ArrayRef("X", (var("i"), var("k"))),
                      ast.ArrayRef("Y", (var("k"), var("j"))))),
        )


class StatementTests(unittest.TestCase):
    def test_scalar_assignment(self) -> None:
        self.assertEqual(parse("s = 0;").items, (ast.Assign(var("s"), num(0)),))

    def test_array_element_assignment(self) -> None:
        stmt = parse("A[i, j] = x * 2;").items[0]
        self.assertEqual(stmt.target, ast.ArrayRef("A", (var("i"), var("j"))))
        self.assertEqual(stmt.value, bin_("*", var("x"), num(2)))

    def test_for_loop(self) -> None:
        loop = parse("for i = 1 to 3 do\n  s = s + i * 2;\nend").items[0]
        self.assertEqual((loop.var, loop.lo, loop.hi), ("i", 1, 3))
        self.assertEqual(len(loop.body), 1)

    def test_negative_and_zero_trip_bounds(self) -> None:
        loop = parse("for i = -3 to -1 do end").items[0]
        self.assertEqual((loop.lo, loop.hi, loop.body), (-3, -1, ()))
        loop = parse("for i = 5 to 1 do s = 1; end").items[0]
        self.assertEqual((loop.lo, loop.hi), (5, 1))

    def test_empty_loop_body(self) -> None:
        self.assertEqual(parse("for i = 1 to 3 do end").items[0].body, ())

    def test_nested_loops(self) -> None:
        loop = parse("for i = 1 to 2 do\n for j = 1 to 3 do\n  s = s + 1;\n end\nend").items[0]
        inner = loop.body[0]
        self.assertIsInstance(inner, ast.ForLoop)
        self.assertEqual((inner.var, len(inner.body)), ("j", 1))

    def test_statements_after_a_loop_and_between_loops(self) -> None:
        prog = parse("for i = 1 to 2 do end\ns = 1;\nfor i = 1 to 2 do end")
        self.assertEqual([type(i) for i in prog.items], [ast.ForLoop, ast.Assign, ast.ForLoop])

    def test_no_semicolon_after_end(self) -> None:
        err = syntax_error("for i = 1 to 2 do end;")
        self.assertEqual(err.message, "expected a statement")

    def test_empty_program_and_comment_only_program(self) -> None:
        self.assertEqual(parse("").items, ())
        self.assertEqual(parse("# nothing here\n").items, ())
        self.assertEqual(parse("   \n\n").items, ())

    def test_comments_are_ignored(self) -> None:
        a = parse("s = 0;  # init\n# next\nfor i = 1 to 2 do # loop\n s = s + 1; # body\nend # done")
        b = parse("s = 0; for i = 1 to 2 do s = s + 1; end")
        self.assertEqual(a, b)

    def test_crlf_source(self) -> None:
        src = "s = 0;\r\nfor i = 1 to 2 do\r\n  s = s + 1;\r\nend\r\n"
        self.assertEqual(parse(src), parse(src.replace("\r\n", "\n")))

    def test_roadmap_scalar_example(self) -> None:
        prog = parse("for i = 1 to 3 do\n s = s + i * 2;\nend")
        loop = prog.items[0]
        self.assertEqual(loop.body[0],
                         ast.Assign(var("s"), bin_("+", var("s"), bin_("*", var("i"), num(2)))))


class PositionTests(unittest.TestCase):
    def test_positions_are_recorded_but_not_compared(self) -> None:
        a = parse("x = 1;")
        b = parse("\n\n   x   =   1 ;")
        self.assertEqual(a, b)
        self.assertEqual((a.items[0].line, a.items[0].col), (1, 1))
        self.assertEqual((b.items[0].line, b.items[0].col), (3, 4))

    def test_node_positions(self) -> None:
        prog = parse("array A[1:2] at 0;\nfor i = 1 to 2 do\n  A[i] = A[i] + 1;\nend")
        decl, loop = prog.items
        self.assertEqual((decl.line, decl.col), (1, 1))
        self.assertEqual((loop.line, loop.col), (2, 1))
        assign = loop.body[0]
        self.assertEqual((assign.line, assign.col), (3, 3))
        self.assertEqual((assign.target.line, assign.target.col), (3, 3))
        add = assign.value
        self.assertEqual((add.op, add.line, add.col), ("+", 3, 15))
        self.assertEqual((add.left.line, add.left.col), (3, 10))  # the array reference starts at its name


class SyntaxErrorTests(unittest.TestCase):
    """Intended messages and positions (docs/language_spec.md Section 9, design decision 43)."""

    def check(self, source: str, line: int, col: int, message: str) -> None:
        err = syntax_error(source)
        self.assertEqual((err.line, err.col, err.message), (line, col, message), source)

    # missing tokens: reported just after the previous token
    def test_missing_semicolon(self) -> None:
        self.check("for i = 1 to 3 do\n  s = s + 1\nend", 2, 12, "expected ';'")

    def test_missing_semicolon_is_reported_on_the_previous_line(self) -> None:
        self.check("s = 1\n\n\nt = 2;", 1, 6, "expected ';'")

    def test_missing_semicolon_at_end_of_file(self) -> None:
        self.check("s = 1", 1, 6, "expected ';' before end of file")
        self.check("array A[1:2] at 0", 1, 18, "expected ';' before end of file")

    def test_unmatched_bracket(self) -> None:
        self.check("A[i, j] = 1;\nx = A[i, j = 1;", 2, 11, "expected ']'")
        self.check("array A[1:2 at 0;", 1, 12, "expected ']'")

    def test_unmatched_parenthesis(self) -> None:
        self.check("x = (1 + 2;", 1, 11, "expected ')'")
        self.check("x = ((1 + 2);", 1, 13, "expected ')'")

    def test_missing_do(self) -> None:
        self.check("for i = 1 to 3\n  s = 1;\nend", 1, 15, "expected 'do'")

    def test_missing_end_at_end_of_file(self) -> None:
        self.check("for i = 1 to 3 do\n  s = 1;\n", 2, 9, "expected 'end' before end of file")
        self.check("for i = 1 to 3 do", 1, 18, "expected 'end' before end of file")
        self.check("for i = 1 to 2 do\n for j = 1 to 2 do\n  s = 1;\nend", 4, 4,
                   "expected 'end' before end of file")

    def test_two_statements_without_separator(self) -> None:
        self.check("x = 3 4;", 1, 6, "expected ';'")
        self.check("x = y = 3;", 1, 6, "expected ';'")  # assignment is not an expression

    def test_missing_equals(self) -> None:
        self.check("x 1;", 1, 2, "expected '='")
        self.check("A[1] + 2;", 1, 5, "expected '='")

    def test_missing_pieces_of_a_loop_header(self) -> None:
        self.check("for = 1 to 2 do end", 1, 4, "expected a loop variable name")
        self.check("for i 1 to 2 do end", 1, 6, "expected '='")
        self.check("for i = 1 2 do end", 1, 10, "expected 'to'")

    def test_missing_pieces_of_a_declaration(self) -> None:
        self.check("array [1:2] at 0;", 1, 6, "expected an array name")
        self.check("array A 1:2] at 0;", 1, 8, "expected '['")
        self.check("array A[1 2] at 0;", 1, 10, "expected ':'")
        self.check("array A[1:2] 0;", 1, 13, "expected 'at'")

    # unexpected tokens: reported at the token
    def test_expected_an_expression(self) -> None:
        self.check("x = ;", 1, 5, "expected an expression")
        self.check("x = a + ;", 1, 9, "expected an expression")
        self.check("x = A[];", 1, 7, "expected an expression")
        self.check("x = A[1,];", 1, 9, "expected an expression")
        self.check("x = * 2;", 1, 5, "expected an expression")
        self.check("x = (1 + );", 1, 10, "expected an expression")

    def test_expected_an_expression_at_end_of_file(self) -> None:
        self.check("x =", 1, 4, "expected an expression before end of file")
        self.check("x = 1 +", 1, 8, "expected an expression before end of file")

    def test_expected_a_statement(self) -> None:
        self.check("1 = x;", 1, 1, "expected a statement")
        self.check("= 3;", 1, 1, "expected a statement")
        self.check(";", 1, 1, "expected a statement")
        self.check("s = 1;\n)", 2, 1, "expected a statement")
        self.check("for i = 1 to 2 do\n  ]\nend", 2, 3, "expected a statement")

    def test_stray_end(self) -> None:
        self.check("end", 1, 1, "unexpected 'end' (no matching 'for')")
        self.check("for i = 1 to 2 do end\nend", 2, 1, "unexpected 'end' (no matching 'for')")

    def test_declaration_inside_a_loop(self) -> None:
        self.check("for i = 1 to 2 do\n  array A[1:2] at 0;\nend", 2, 3,
                   "array declarations are only allowed at the top level")

    def test_keywords_cannot_be_names(self) -> None:
        self.check("for = 1;", 1, 4, "expected a loop variable name")
        self.check("to = 1;", 1, 1, "expected a statement")

    # static arrays only
    def test_variable_array_bounds(self) -> None:
        self.check("array B[1:n] at 0;", 1, 11, BOUND_MESSAGE)
        self.check("array B[n:5] at 0;", 1, 9, BOUND_MESSAGE)
        self.check("array B[1:2, 1:m] at 0;", 1, 16, BOUND_MESSAGE)
        self.check("array B[1:2*3] at 0;", 1, 12, "expected ']'")  # only literals, not expressions
        self.check("array B[1:(2)] at 0;", 1, 11, BOUND_MESSAGE)
        self.check("array B[-n:5] at 0;", 1, 10, BOUND_MESSAGE)
        self.assertIn("variable-bound arrays are not supported", BOUND_MESSAGE)

    def test_bounds_with_nothing_after_a_minus_or_a_comma(self) -> None:
        self.check("array B[1:2, ] at 0;", 1, 14, BOUND_MESSAGE)
        self.check("array B[-:2] at 0;", 1, 10, BOUND_MESSAGE)
        self.check("array B[-", 1, 10, "expected an integer constant before end of file")

    def test_non_constant_base_address(self) -> None:
        self.check("array B[1:2] at n;", 1, 17, BASE_MESSAGE)
        self.check("array B[1:2] at -n;", 1, 18, BASE_MESSAGE)
        self.check("array B[1:2] at", 1, 16, "expected an integer constant before end of file")

    def test_non_constant_loop_bounds(self) -> None:
        self.check("for i = 1 to n do end", 1, 14, LOOP_BOUND_MESSAGE)
        self.check("for i = n to 3 do end", 1, 9, LOOP_BOUND_MESSAGE)
        self.check("for i = 1 to 2 + 1 do end", 1, 15, "expected 'do'")
        self.check("for i = A[1] to 3 do end", 1, 9, LOOP_BOUND_MESSAGE)

    # the lexer's errors surface through parse()
    def test_illegal_character_through_parse(self) -> None:
        self.check("s = 3 $ 4;", 1, 7, "illegal character '$'")

    def test_first_error_only(self) -> None:
        self.check("x = ;\ny = ;", 1, 5, "expected an expression")

    def test_formatted_message(self) -> None:
        err = syntax_error("a = 1;\nb = 2;\nc = 3;\n  s = s + 1\nend")
        self.assertEqual(err.format("prog.ml"), "prog.ml: line 4, col 12: syntax error: expected ';'")

    def test_error_has_the_common_base_class(self) -> None:
        from arraypeep.symbol_table import ArrayPeepError

        self.assertIsInstance(syntax_error("x ="), ArrayPeepError)


class ExamplesTests(unittest.TestCase):
    def test_every_example_parses(self) -> None:
        self.assertEqual(len(EXAMPLES), 5)
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                prog = parse(path.read_text(encoding="utf-8"))
                self.assertGreater(len(prog.items), 0)

    def test_example_structure(self) -> None:
        def structure(name):
            prog = parse((ROOT / "examples" / name).read_text(encoding="utf-8"))
            return [type(i).__name__ for i in prog.items]

        self.assertEqual(structure("1d_sum.ml"), ["ArrayDecl", "ForLoop", "Assign", "ForLoop"])
        self.assertEqual(structure("2d_traversal.ml"), ["ArrayDecl", "Assign", "ForLoop"])
        self.assertEqual(structure("3d_traversal.ml"), ["ArrayDecl", "Assign", "ForLoop"])
        self.assertEqual(structure("linear_subscripts.ml"), ["ArrayDecl", "Assign", "ForLoop"])
        self.assertEqual(structure("matrix_multiply.ml"),
                         ["ArrayDecl", "ArrayDecl", "ArrayDecl", "ForLoop"])

    def test_text_dump_of_every_example(self) -> None:
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                dump = ast.dump_program(parse(path.read_text(encoding="utf-8")))
                self.assertTrue(dump.startswith("Program\n"))
                self.assertIn("ArrayDecl", dump)
                self.assertIn("ForLoop", dump)

    def test_dump_of_a_small_program(self) -> None:
        prog = parse("array A[1:2] at 5;\nfor i = 1 to 2 do\n  A[i] = -i + 1;\nend")
        self.assertEqual(
            ast.dump_program(prog),
            "\n".join([
                "Program",
                "  ArrayDecl A [1:2] at 5  (line 1)",
                "  ForLoop i = 1 to 2  (line 2)",
                "    Assign  (line 3)",
                "      target: ArrayRef A (1 subscript(s))",
                "        ScalarRef i",
                "      value: BinOp +",
                "        UnaryMinus",
                "          ScalarRef i",
                "        Number 1",
            ]),
        )


class RoundTripTests(unittest.TestCase):
    """Roadmap Phase 3: parse, pretty-print, parse again gives an equal tree."""

    def test_examples(self) -> None:
        for path in EXAMPLES:
            with self.subTest(example=path.name):
                prog = parse(path.read_text(encoding="utf-8"))
                text = ast.format_program(prog)
                self.assertEqual(parse(text), prog)
                self.assertEqual(ast.format_program(parse(text)), text)  # printing is stable

    def test_expression_forms(self) -> None:
        for text in ["a - (b - c)", "a - b - c", "a + (b + c)", "(a + b) * c", "a * (b + c)",
                     "-(a + b)", "- -x", "-5", "a - -b", "-a * b", "a * -b", "-(a * b)",
                     "(a * b) * c", "a * (b * c)", "A[i + 1, (j - 1) * 2]", "V[W[i] - 1]"]:
            with self.subTest(expr=text):
                tree = expr_of(text)
                again = expr_of(ast.format_expr(tree))
                self.assertEqual(again, tree)

    def test_minimal_parentheses(self) -> None:
        for text, expected in [("(a + b) + c", "a + b + c"), ("a + (b * c)", "a + b * c"),
                               ("(a * b) + c", "a * b + c"), ("a - (b - c)", "a - (b - c)"),
                               ("((a))", "a"), ("a * (b + c)", "a * (b + c)")]:
            with self.subTest(expr=text):
                self.assertEqual(ast.format_expr(expr_of(text)), expected)

    def test_printed_layout(self) -> None:
        prog = parse("array A[1:10,1:20] at 1000;for i=1 to 2 do for j=1 to 2 do A[i,j]=i+j; end end")
        self.assertEqual(
            ast.format_program(prog),
            "array A[1:10, 1:20] at 1000;\n"
            "for i = 1 to 2 do\n"
            "  for j = 1 to 2 do\n"
            "    A[i, j] = i + j;\n"
            "  end\n"
            "end\n",
        )
        self.assertEqual(ast.format_program(parse("")), "")

    def test_random_expressions(self) -> None:
        rnd = random.Random(2026)

        def gen(depth: int) -> ast.Expr:
            r = rnd.random()
            if depth == 0 or r < 0.25:
                return num(rnd.randint(0, 20)) if rnd.random() < 0.5 else var(rnd.choice("abij"))
            if r < 0.4:
                return ast.UnaryMinus(gen(depth - 1))
            if r < 0.5:
                n = rnd.randint(1, 3)
                return ast.ArrayRef(rnd.choice("AB"), tuple(gen(depth - 1) for _ in range(n)))
            return ast.BinOp(rnd.choice("+-*"), gen(depth - 1), gen(depth - 1))

        for _ in range(500):
            tree = gen(rnd.randint(0, 5))
            text = ast.format_expr(tree)
            self.assertEqual(expr_of(text), tree, text)

    def test_random_programs(self) -> None:
        rnd = random.Random(7)

        def expr() -> ast.Expr:
            return ast.BinOp(rnd.choice("+-*"), var(rnd.choice("st")), num(rnd.randint(0, 9)))

        def stmts(depth: int) -> tuple:
            out = []
            for _ in range(rnd.randint(0, 3)):
                if depth < 3 and rnd.random() < 0.4:
                    lo = rnd.randint(-3, 3)
                    out.append(ast.ForLoop(f"i{depth}", lo, lo + rnd.randint(-2, 4), stmts(depth + 1)))
                else:
                    out.append(ast.Assign(var(rnd.choice("st")), expr()))
            return tuple(out)

        for _ in range(100):
            prog = ast.Program(
                (ast.ArrayDecl("A", ((rnd.randint(-5, 0), rnd.randint(1, 5)),), rnd.randint(-9, 9)),)
                + stmts(0)
            )
            self.assertEqual(parse(ast.format_program(prog)), prog)


class FrontEndIndependenceTests(unittest.TestCase):
    """Roadmap Phase 3: no code generation exists; the parser knows nothing about quadruples."""

    def test_front_end_does_not_load_quadruple_or_codegen_modules(self) -> None:
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "import arraypeep.lexer, arraypeep.ast_nodes, arraypeep.parser, arraypeep.semantic;"
            "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))"
        )
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)],
                             capture_output=True, text=True, check=True).stdout
        for forbidden in ("quad", "codegen", "interpreter", "optimizer", "driver"):
            self.assertNotIn(f"arraypeep.{forbidden}", out)


if __name__ == "__main__":
    unittest.main()
