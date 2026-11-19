"""Tests for arraypeep.linear_analysis and the Phase 7 part of arraypeep.optimizer (roadmap Phase 7).

The expected forms come from the roadmap table (page 21) and hand calculation;
the evaluator of expression trees in this file is independent of the code under test.
"""
import dataclasses
import itertools
import pathlib
import random
import subprocess
import sys
import unittest

from arraypeep import ast_nodes as ast
from arraypeep.codegen import generate
from arraypeep.interpreter import execute
from arraypeep.linear_analysis import (
    LinearForm,
    NonLinear,
    REASON_ARRAY,
    REASON_PRODUCT,
    REASON_SCALAR,
    analyze_subscript,
)
from arraypeep.optimizer import (
    STATUS_FALLBACK,
    STATUS_OPTIMIZABLE,
    WindowError,
    analyze_accesses,
    enclosing_loops,
    format_report,
    identify_window,
    validate_windows,
)
from arraypeep.parser import parse
from arraypeep.quad import TAG_ADDR, TAG_BODY, Quad
from arraypeep.semantic import analyze
from arraypeep.symbol_table import ArrayPeepError

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))
LEVELS = ("L0", "L1")


def expr_of(text: str) -> ast.Expr:
    """Expression tree of ``text`` (parsed as the right side of an assignment)."""
    return parse(f"x = {text};").items[0].value


def form_of(text: str, loop_vars=("i", "j")):
    return analyze_subscript(expr_of(text), list(loop_vars))


def gen(source: str, level: str = "L1"):
    program = parse(source)
    table = analyze(program)
    return generate(program, table, level), table


def direct(e: ast.Expr, env: dict[str, int]) -> int:
    """Value of a linear-candidate expression tree (no array elements)."""
    if isinstance(e, ast.Number):
        return e.value
    if isinstance(e, ast.ScalarRef):
        return env[e.name]
    if isinstance(e, ast.UnaryMinus):
        return -direct(e.operand, env)
    a, b = direct(e.left, env), direct(e.right, env)
    return a + b if e.op == "+" else a - b if e.op == "-" else a * b


# -- the roadmap table -------------------------------------------------------------------------------


class RoadmapTableTests(unittest.TestCase):
    def test_linear_rows(self) -> None:
        rows = [
            ("i", 0, {"i": 1}, "0 ; i:1"),
            ("2*i + 1", 1, {"i": 2}, "1 ; i:2"),
            ("3 - j", 3, {"j": -1}, "3 ; j:-1"),
            ("(i + 1) * 4", 4, {"i": 4}, "4 ; i:4"),
            ("i + j", 0, {"i": 1, "j": 1}, "0 ; i:1, j:1"),
            ("5", 5, {}, "5 ; (no loop variables)"),
        ]
        for text, const, coeffs, shown in rows:
            with self.subTest(expr=text):
                f = form_of(text)
                self.assertIsInstance(f, LinearForm)
                self.assertEqual((f.const, f.coeffs), (const, coeffs))
                self.assertEqual(f.table_text(), shown)

    def test_nonlinear_rows(self) -> None:
        f = form_of("i * j")
        self.assertEqual((f.reason, f.text), (REASON_PRODUCT, "NOT LINEAR: product of loop variables"))
        f = form_of("k")
        self.assertEqual((f.reason, f.text), (REASON_SCALAR, "NOT LINEAR: variable that is not a loop variable"))
        self.assertEqual(f.detail, "k")
        f = form_of("B[i]")
        self.assertEqual((f.reason, f.text), (REASON_ARRAY, "NOT LINEAR: array element in subscript"))
        self.assertEqual(f.detail, "B[i]")
        self.assertEqual(form_of("i * j").describe(), "NOT LINEAR: product of loop variables (i * j)")

    def test_report_form_of_the_specification(self) -> None:
        self.assertEqual(form_of("2*i + 1").expr_text(), "1 + 2*i")
        self.assertEqual(form_of("j - 1").expr_text(), "-1 + 1*j")
        self.assertEqual(form_of("3 - j").expr_text(), "3 - 1*j")
        self.assertEqual(form_of("5").expr_text(), "5")


# -- more linear and non-linear shapes -------------------------------------------------------------------


class ExtractorTests(unittest.TestCase):
    def check(self, text, const, coeffs, loop_vars=("i", "j", "k")):
        f = form_of(text, loop_vars)
        self.assertIsInstance(f, LinearForm, text)
        self.assertEqual((f.const, f.coeffs), (const, coeffs), text)

    def test_more_linear_forms(self) -> None:
        self.check("-(i - 1)", 1, {"i": -1})
        self.check("- i", 0, {"i": -1})
        self.check("2 * (3 - j)", 6, {"j": -2})
        self.check("(i + 1) * (2 + 2)", 4, {"i": 4})
        self.check("3 * (2 * i)", 0, {"i": 6})
        self.check("i + i", 0, {"i": 2})
        self.check("i * 3 - 2 * j + k + 7", 7, {"i": 3, "j": -2, "k": 1})
        self.check("2 - -3", 5, {})
        self.check("(((i)))", 0, {"i": 1})
        self.check("11 - i", 11, {"i": -1})

    def test_cancelled_and_zero_coefficients_are_dropped(self) -> None:
        self.check("i - i", 0, {})
        self.check("i * 0 + 5", 5, {})
        self.check("0 * (i + j)", 0, {})
        self.check("(i + j) - j", 0, {"i": 1})

    def test_coefficients_follow_loop_order_not_source_order(self) -> None:
        f = form_of("j + i", ("i", "j"))
        self.assertEqual(list(f.coeffs), ["i", "j"])
        f = form_of("j + i", ("j", "i"))
        self.assertEqual(list(f.coeffs), ["j", "i"])

    def test_more_nonlinear_shapes(self) -> None:
        for text, reason in [
            ("(i + 1) * (j + 1)", REASON_PRODUCT),
            ("i * i", REASON_PRODUCT),
            ("2 * i * j", REASON_PRODUCT),
            ("B[i] + 1", REASON_ARRAY),
            ("i + B[i + 1]", REASON_ARRAY),
            ("i + k", REASON_SCALAR),
            ("3 * k", REASON_SCALAR),
            ("-k", REASON_SCALAR),
        ]:
            with self.subTest(expr=text):
                f = form_of(text)
                self.assertIsInstance(f, NonLinear)
                self.assertEqual(f.reason, reason)

    def test_first_problem_from_the_left_is_reported(self) -> None:
        self.assertEqual(form_of("k + i * j").reason, REASON_SCALAR)
        self.assertEqual(form_of("i * j + k").reason, REASON_PRODUCT)

    def test_a_name_outside_the_enclosing_loops_is_a_plain_scalar(self) -> None:
        self.assertEqual(form_of("i", ()).reason, REASON_SCALAR)
        self.assertEqual(form_of("j", ("i",)).reason, REASON_SCALAR)
        self.assertEqual(form_of("7", ()).const, 7)

    def test_unsupported_node_gives_a_reason_not_an_exception(self) -> None:
        class Odd(ast.Node):
            pass

        f = analyze_subscript(Odd(), ["i"])
        self.assertIsInstance(f, NonLinear)
        self.assertIn("unsupported expression", f.text)

    def test_property_extracted_form_equals_the_tree(self) -> None:
        rng = random.Random(7)

        def make(depth: int, linear_only: bool) -> ast.Expr:
            if depth == 0 or rng.random() < 0.25:
                return rng.choice([ast.Number(rng.randint(0, 6)), ast.ScalarRef("i"), ast.ScalarRef("j"), ast.ScalarRef("k")])
            kind = rng.choice(["+", "-", "*", "neg"])
            if kind == "neg":
                return ast.UnaryMinus(make(depth - 1, linear_only))
            left = make(depth - 1, linear_only)
            if kind == "*" and linear_only:
                const = ast.Number(rng.randint(0, 5))
                return ast.BinOp("*", const, left) if rng.random() < 0.5 else ast.BinOp("*", left, const)
            return ast.BinOp(kind, left, make(depth - 1, linear_only))

        envs = [{"i": a, "j": b, "k": c} for a, b, c in itertools.product((-3, 0, 4), (1, 9), (-2, 5))]
        linear = other = 0
        for _ in range(400):
            tree = make(4, linear_only=False)
            form = analyze_subscript(tree, ["i", "j", "k"])
            if isinstance(form, LinearForm):
                linear += 1
                for env in envs:
                    self.assertEqual(form.evaluate(env), direct(tree, env), ast.format_expr(tree))
            else:
                other += 1
            tree = make(4, linear_only=True)
            form = analyze_subscript(tree, ["i", "j", "k"])
            self.assertIsInstance(form, LinearForm, ast.format_expr(tree))
            for env in envs:
                self.assertEqual(form.evaluate(env), direct(tree, env), ast.format_expr(tree))
        self.assertGreater(linear, 50)
        self.assertGreater(other, 50)

    def test_roadmap_subscripts_evaluate_correctly(self) -> None:
        for text in ("2*i + 1", "3 - j", "(i + 1) * 4", "i + j", "i", "5"):
            tree = expr_of(text)
            f = analyze_subscript(tree, ["i", "j"])
            for i, j in itertools.product(range(-2, 4), range(1, 4)):
                self.assertEqual(f.evaluate({"i": i, "j": j}), direct(tree, {"i": i, "j": j}), text)


# -- enclosing loops ------------------------------------------------------------------------------


class EnclosingLoopTests(unittest.TestCase):
    SRC = ("array A[1:9, 1:9] at 0;\narray V[1:9] at 100;\n"
           "for i = 1 to 3 do\n  V[i] = 1;\n  for j = 2 to 4 do\n    for k = 5 to 6 do\n      s = s + A[j, k];\n    end\n    s = s + V[j];\n  end\n"
           "  s = s + V[i];\nend\n"
           "s = V[2];\n"
           "for j = 7 to 8 do\n  s = s + V[j];\nend")

    def test_loops_per_access_outermost_first(self) -> None:
        code, _ = gen(self.SRC)
        reports = analyze_accesses(code)
        got = [(r.source, [(e.var, e.lo, e.hi, e.depth) for e in r.loops]) for r in reports]
        self.assertEqual(
            got,
            [
                ("V[i]", [("i", 1, 3, 1)]),
                ("A[j, k]", [("i", 1, 3, 1), ("j", 2, 4, 2), ("k", 5, 6, 3)]),
                ("V[j]", [("i", 1, 3, 1), ("j", 2, 4, 2)]),
                ("V[i]", [("i", 1, 3, 1)]),
                ("V[2]", []),
                ("V[j]", [("j", 7, 8, 1)]),  # a later, unrelated loop that reuses the name j
            ],
        )

    def test_a_loop_variable_of_a_closed_loop_is_not_in_scope(self) -> None:
        code, _ = gen("array V[1:9] at 0;\nfor j = 1 to 3 do s = s + 1; end\nfor i = 1 to 3 do s = s + V[i]; end")
        (r,) = analyze_accesses(code)
        self.assertEqual([e.var for e in r.loops], ["i"])

    def test_enclosing_loops_function_on_positions(self) -> None:
        code, _ = gen(self.SRC)
        a = next(d for d in code.accesses if d.array == "A")
        self.assertEqual([e.var for e in enclosing_loops(code.loops, a.access)], ["i", "j", "k"])
        self.assertEqual(enclosing_loops(code.loops, 1), ())


# -- eligibility ------------------------------------------------------------------------------------


class EligibilityTests(unittest.TestCase):
    DECL = "array A[1:10, 1:10] at 0;\narray B[1:10] at 200;\n"

    def one(self, body: str, loops=("i", "j")):
        head = "".join(f"for {v} = 1 to 3 do " for v in loops)
        code, _ = gen(self.DECL + head + body + " end" * len(loops))
        return analyze_accesses(code)

    def test_all_linear_is_optimizable(self) -> None:
        (r,) = self.one("s = s + A[i + 1, 2 * j];")
        self.assertEqual((r.status, r.reason, r.optimizable), (STATUS_OPTIMIZABLE, None, True))
        self.assertTrue(all(isinstance(f, LinearForm) for f in r.forms))

    def test_one_nonlinear_subscript_makes_the_whole_access_a_fallback(self) -> None:
        (r,) = self.one("s = s + A[i, j * j];")
        self.assertEqual((r.status, r.optimizable), (STATUS_FALLBACK, False))
        self.assertIsInstance(r.forms[0], LinearForm)  # the linear subscript is still analysed
        self.assertIsInstance(r.forms[1], NonLinear)
        self.assertEqual(r.reason, "subscript 2: NOT LINEAR: product of loop variables (j * j)")

    def test_first_nonlinear_subscript_names_the_reason(self) -> None:
        r, inner = self.one("s = s + A[k, B[i]];")  # the outer access first, then the nested B[i]
        self.assertEqual((inner.source, inner.status), ("B[i]", STATUS_OPTIMIZABLE))
        self.assertEqual(r.reason, "subscript 1: NOT LINEAR: variable that is not a loop variable (k)")
        self.assertEqual([type(f).__name__ for f in r.forms], ["NonLinear", "NonLinear"])

    def test_roadmap_cases_t12_t13_t14(self) -> None:
        code, _ = gen("array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i * j]; end end")
        self.assertIn(REASON_PRODUCT, analyze_accesses(code)[0].reason)
        code, _ = gen("array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 9 do W[i] = 9 - i; end\n"
                      "for i = 0 to 9 do s = s + V[W[i]]; end")
        reports = analyze_accesses(code)
        self.assertEqual([(r.source, r.status) for r in reports],
                         [("W[i]", STATUS_OPTIMIZABLE), ("V[W[i]]", STATUS_FALLBACK), ("W[i]", STATUS_OPTIMIZABLE)])
        self.assertIn(REASON_ARRAY, reports[1].reason)
        code, _ = gen("array A[1:10, 1:10] at 0;\nk = 2;\nfor i = 1 to 4 do s = s + A[i + k, 1]; end")
        (r,) = analyze_accesses(code)
        self.assertIn(REASON_SCALAR, r.reason)

    def test_constant_address_outside_any_loop_is_optimizable(self) -> None:
        code, _ = gen("array A[1:10, 1:20] at 1000;\nx = A[3, 4];")
        (r,) = analyze_accesses(code)
        self.assertEqual((r.status, r.loops), (STATUS_OPTIMIZABLE, ()))
        self.assertEqual([f.const for f in r.forms], [3, 4])

    def test_writes_and_reads_are_distinguished(self) -> None:
        code, _ = gen("array V[1:9] at 0;\nfor i = 1 to 3 do V[i] = V[i] + 1; end")
        self.assertEqual([(r.source, r.is_write) for r in analyze_accesses(code)], [("V[i]", False), ("V[i]", True)])

    def test_analysis_works_at_both_levels(self) -> None:
        src = "array A[1:10, 1:10] at 0;\nfor i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j * j]; end end"
        a, b = (analyze_accesses(gen(src, level)[0]) for level in LEVELS)
        self.assertEqual([(r.status, r.reason) for r in a], [(r.status, r.reason) for r in b])
        self.assertNotEqual(a[0].window.last, b[0].window.last)  # L0 has the longer window


# -- the five examples ----------------------------------------------------------------------------------


class ExampleTests(unittest.TestCase):
    EXPECTED_COUNTS = {"1d_sum": 2, "2d_traversal": 1, "3d_traversal": 1, "linear_subscripts": 1, "matrix_multiply": 5}

    def test_every_access_of_every_example(self) -> None:
        self.assertEqual(len(EXAMPLES), 5)
        for path in EXAMPLES:
            for level in LEVELS:
                code, _ = gen(path.read_text(encoding="utf-8"), level)
                reports = analyze_accesses(code)
                with self.subTest(example=path.name, level=level):
                    self.assertEqual(len(reports), self.EXPECTED_COUNTS[path.stem])
                    self.assertEqual(len(reports), len(code.accesses))
                    self.assertTrue(all(r.optimizable and r.reason is None for r in reports))
                    for r in reports:
                        self.assertEqual(len(r.forms), len(code.accesses[r.index].subscripts))

    def test_linear_subscripts_example(self) -> None:
        code, _ = gen((ROOT / "examples" / "linear_subscripts.ml").read_text(encoding="utf-8"))
        (r,) = analyze_accesses(code)
        self.assertEqual([f.table_text() for f in r.forms], ["1 ; i:2", "-1 ; j:1"])
        self.assertEqual([(e.var, e.lo, e.hi) for e in r.loops], [("i", 1, 4), ("j", 2, 20)])
        self.assertEqual((r.window.first, r.window.last, r.window.access), (4, 8, 9))  # after FOR, FOR and s = 0

    def test_matrix_multiply_loop_nesting_per_access(self) -> None:
        code, _ = gen((ROOT / "examples" / "matrix_multiply.ml").read_text(encoding="utf-8"))
        got = [(r.source, r.is_write, "".join(e.var for e in r.loops)) for r in analyze_accesses(code)]
        self.assertEqual(got, [("Z[i, j]", True, "ij"), ("Z[i, j]", False, "ijk"), ("X[i, k]", False, "ijk"),
                               ("Y[k, j]", False, "ijk"), ("Z[i, j]", True, "ijk")])

    def test_1d_sum_has_empty_windows_at_l1(self) -> None:
        code, _ = gen((ROOT / "examples" / "1d_sum.ml").read_text(encoding="utf-8"), "L1")
        for r in analyze_accesses(code):
            self.assertEqual((r.window.positions, r.window.addr), (range(r.window.access, r.window.access), ()))


# -- windows ------------------------------------------------------------------------------------


NESTED = ("array V[0:9] at 0;\narray W[0:9] at 100;\n"
          "for i = 0 to 9 do W[i] = 9 - i; end\nfor i = 0 to 3 do s = s + V[W[i] + 1 - 1]; end")


class WindowTests(unittest.TestCase):
    def test_windows_are_exact_on_the_examples(self) -> None:
        for path in EXAMPLES:
            for level in LEVELS:
                code, _ = gen(path.read_text(encoding="utf-8"), level)
                reports = analyze_accesses(code)
                with self.subTest(example=path.name, level=level):
                    addr_all = {p for p, q in enumerate(code.quads, 1) if q.tag == TAG_ADDR}
                    union: set[int] = set()
                    for r in reports:
                        w = r.window
                        self.assertEqual(w.last + 1, w.access)
                        self.assertEqual(list(w.positions), list(range(w.first, w.access)))  # contiguous
                        self.assertEqual(set(w.addr), set(w.positions))  # exactly the ADDR quads
                        self.assertTrue(all(code.quads[p - 1].tag == TAG_ADDR for p in w.positions))
                        self.assertEqual(w.nested, ())
                        self.assertFalse(union & set(w.addr))
                        union |= set(w.addr)
                    self.assertEqual(union, addr_all)  # every ADDR quadruple belongs to exactly one access

    def test_exact_boundaries_of_the_roadmap_example(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end"
        w1 = analyze_accesses(gen(src, "L1")[0])[0].window
        w0 = analyze_accesses(gen(src, "L0")[0])[0].window
        self.assertEqual((w1.first, w1.last, w1.access, w1.addr), (3, 4, 5, (3, 4)))
        self.assertEqual((w0.first, w0.last, w0.access, w0.addr), (3, 7, 8, (3, 4, 5, 6, 7)))

    def test_nested_access_window(self) -> None:
        code, _ = gen(NESTED)
        reports = analyze_accesses(code)
        outer = next(r for r in reports if r.array == "V")
        inner = next(r for r in reports if r.array == "W" and r.index == outer.index - 1)
        self.assertEqual(outer.window.nested, (inner.window.access,))
        self.assertIn(inner.window.access, outer.window.positions)  # the nested read lies inside
        self.assertEqual(code.quads[inner.window.access - 1].tag, TAG_BODY)
        self.assertFalse(set(outer.window.addr) & set(range(inner.window.first, inner.window.access + 1)))
        self.assertEqual(set(outer.window.addr) | {inner.window.access} | set(inner.window.addr), set(outer.window.positions))
        self.assertEqual((outer.status, inner.status), (STATUS_FALLBACK, STATUS_OPTIMIZABLE))
        self.assertEqual([r.index for r in reports if r.array == "V"], [outer.index])
        self.assertLess(reports.index(outer), reports.index(inner))  # outer access first, as in the source

    def test_identify_window_matches_the_descriptor(self) -> None:
        code, _ = gen(NESTED)
        for k, d in enumerate(code.accesses):
            w = identify_window(code.quads, code.accesses, k)
            self.assertEqual((w.first, w.last, w.access), (d.first, d.last, d.access))

    def test_valid_listings_pass_validation(self) -> None:
        for src in [NESTED, (ROOT / "examples" / "matrix_multiply.ml").read_text(encoding="utf-8")]:
            for level in LEVELS:
                code, _ = gen(src, level)
                validate_windows(code.quads, code.accesses)

    def corrupt(self, change, src=None, level="L1"):
        code, _ = gen(src or "array A[1:10, 1:20] at 1000;\nfor i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j]; end end", level)
        accesses = list(code.accesses)
        accesses[0] = change(accesses[0], code)
        return code.quads, accesses

    def assertInternal(self, quads, accesses, fragment: str) -> None:
        with self.assertRaises(WindowError) as ctx:
            validate_windows(quads, accesses)
        self.assertTrue(ctx.exception.message.startswith("internal error: "), ctx.exception.message)
        self.assertIn(fragment, ctx.exception.message)
        self.assertIsInstance(ctx.exception, ArrayPeepError)
        with self.assertRaises(WindowError):
            identify_window(quads, accesses, 0)

    def test_window_not_ending_before_the_access(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, last=d.last - 1))
        self.assertInternal(q, a, "window ends at")

    def test_access_beyond_the_listing(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, first=d.first, last=len(c.quads) + 4, access=len(c.quads) + 5))
        self.assertInternal(q, a, "outside the listing")
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, access=0, last=-1))
        self.assertInternal(q, a, "outside the listing")

    def test_access_position_not_an_indexed_quadruple(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, access=d.access - 1, last=d.last - 1))
        self.assertInternal(q, a, "expected '=[ ]'")

    def test_read_marked_as_write(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, is_write=True))
        self.assertInternal(q, a, "expected '[ ]='")

    def test_window_starting_after_the_access(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, first=d.access + 1))
        self.assertInternal(q, a, "window starts at")

    def test_window_swallowing_a_body_quadruple(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, first=d.first - 1))  # takes in the FOR j quadruple
        self.assertInternal(q, a, "not an ADDR quadruple or a nested read")

    def test_window_missing_an_addr_quadruple(self) -> None:
        q, a = self.corrupt(lambda d, c: dataclasses.replace(d, first=d.first + 1))
        self.assertInternal(q, a, "belong to no access window")

    def test_missing_and_duplicate_descriptors(self) -> None:
        code, _ = gen("array V[1:9] at 0;\nfor i = 1 to 3 do V[i] = V[i] + 1; end")
        with self.assertRaises(WindowError) as ctx:
            validate_windows(code.quads, code.accesses[:1])
        self.assertIn("no access descriptor", ctx.exception.message)
        with self.assertRaises(WindowError) as ctx:
            validate_windows(code.quads, [code.accesses[0], code.accesses[0], code.accesses[1]])
        self.assertIn("already used", ctx.exception.message)

    def test_partially_overlapping_windows(self) -> None:
        code, _ = gen("array A[1:9, 1:9] at 0;\nfor i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j] + A[j, i]; end end")
        a, b = code.accesses
        shifted = dataclasses.replace(b, first=a.first + 1)  # starts inside the first window, ends after it
        shifted = dataclasses.replace(shifted, first=min(shifted.first, shifted.access))
        with self.assertRaises(WindowError) as ctx:
            validate_windows(code.quads, [a, shifted])
        self.assertIn("overlap partially", ctx.exception.message)

    def test_analyze_accesses_stops_on_a_bad_listing(self) -> None:
        code, _ = gen("array V[1:9] at 0;\nfor i = 1 to 3 do s = s + V[i + 1]; end")
        bad = dataclasses.replace(code, accesses=[dataclasses.replace(code.accesses[0], last=0)])
        with self.assertRaises(WindowError):
            analyze_accesses(bad)


# -- fallback programs, report text, independence ------------------------------------------------------------------


FALLBACKS = [
    "array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i * j]; end end",
    "array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 9 do W[i] = 9 - i; end\nfor i = 0 to 9 do s = s + V[W[i]]; end",
    "array A[1:10, 1:10] at 0;\nk = 3;\nfor i = 1 to 4 do s = s + A[i + k, 1]; end",
    "array A[1:10, 1:10] at 0;\nfor i = 1 to 3 do for j = 1 to 3 do A[i, j * j] = i + j; end end",
]


class FallbackTests(unittest.TestCase):
    def test_fallback_programs_are_flagged_and_unchanged_by_the_analysis(self) -> None:
        for src in FALLBACKS:
            with self.subTest(src=src[:40]):
                code, table = gen(src, "L1")
                quads, accesses, loops = list(code.quads), list(code.accesses), list(code.loops)
                reports = analyze_accesses(code)
                self.assertTrue(any(r.status == STATUS_FALLBACK for r in reports))
                self.assertTrue(all(r.reason for r in reports if not r.optimizable))
                self.assertEqual((code.quads, code.accesses, code.loops), (quads, accesses, loops))

    def test_fallback_programs_still_run_and_agree_with_l0(self) -> None:
        for src in FALLBACKS:
            with self.subTest(src=src[:40]):
                (c0, table), (c1, _) = gen(src, "L0"), gen(src, "L1")
                analyze_accesses(c1)
                r0, r1 = execute(c0.quads, table.arrays, "L0"), execute(c1.quads, table.arrays, "L1")
                self.assertEqual((r0.scalars, r0.memory, r0.trace), (r1.scalars, r1.memory, r1.trace))
                self.assertTrue(r1.trace)
                self.assertLessEqual(r1.counters.addr, r0.counters.addr)

    def test_fallback_l1_code_is_the_ordinary_l1_code(self) -> None:
        code, table = gen(FALLBACKS[0], "L1")
        analyze_accesses(code)
        again = generate(parse(FALLBACKS[0]), table, "L1")
        self.assertEqual(code.quads, again.quads)
        self.assertEqual(sorted(execute(code.quads, table.arrays).scalars), ["s"])


class ReportTextTests(unittest.TestCase):
    T04 = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do\n  for j = 2 to 20 do\n    s = s + A[2*i + 1, j - 1];\n  end\nend"

    def test_report_layout(self) -> None:
        code, _ = gen(self.T04)
        text = format_report(analyze_accesses(code))
        self.assertEqual(
            text.splitlines(),
            [
                "== Optimization report " + "=" * 51,
                " access A[2 * i + 1, j - 1]  (read, line 4)",
                "   enclosing    : i = 1..4, j = 2..20",
                "   linear forms : subscript 1 = 1 + 2*i ; subscript 2 = -1 + 1*j",
                "   window       : quadruples 3..7, access 8",
                "   decision     : eligible (all subscripts linear)",
            ],
        )
        self.assertEqual(len(text.splitlines()[0]), 74)

    def test_report_of_a_fallback_and_of_empty_windows(self) -> None:
        code, _ = gen("array V[0:9] at 0;\nfor i = 0 to 9 do s = s + V[i * i]; end")
        text = format_report(analyze_accesses(code))
        self.assertIn("subscript 1 = NOT LINEAR: product of loop variables (i * i)", text)
        self.assertIn("decision     : kept at L1 (subscript 1: NOT LINEAR: product of loop variables (i * i))", text)
        code, _ = gen("array V[0:9] at 0;\nfor i = 0 to 9 do s = s + V[i]; end")
        self.assertIn("window       : empty, access 2", format_report(analyze_accesses(code)))
        self.assertIn("(no loops)", format_report(analyze_accesses(gen("array V[0:9] at 0;\ns = V[3];")[0])))

    def test_report_for_a_program_without_arrays(self) -> None:
        code, _ = gen("for i = 1 to 3 do s = s + i; end")
        self.assertEqual(analyze_accesses(code), [])
        self.assertEqual(format_report([]).splitlines(), ["== Optimization report " + "=" * 51])


class IndependenceTests(unittest.TestCase):
    def loaded(self, module: str) -> str:
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                f"import {module};"
                "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))")
        return subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True).stdout

    def test_linear_analysis_needs_only_the_expression_tree(self) -> None:
        out = self.loaded("arraypeep.linear_analysis")
        for forbidden in ("quad", "codegen", "interpreter", "optimizer", "symbol_table", "lexer", "parser", "semantic"):
            self.assertNotIn(f"arraypeep.{forbidden}'", out)

    def test_optimizer_does_not_load_the_interpreter(self) -> None:
        out = self.loaded("arraypeep.optimizer")
        for forbidden in ("interpreter", "metrics", "precompute", "loop_opt", "driver", "report"):
            self.assertNotIn(f"arraypeep.{forbidden}'", out)


if __name__ == "__main__":
    unittest.main()
