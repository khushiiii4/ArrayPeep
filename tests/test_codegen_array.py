"""Tests for array code generation at levels L0 and L1 (roadmap Phase 5).

Expected addresses come from an independent product-form Eq. 1 written in this
file (not from ``address_math``), and listings are executed by a small
test-local evaluator (the real interpreter is Phase 6). Hand-worked values come
from docs/quad_spec.md Section 7 and docs/hand_examples.md.
"""
import itertools
import random
import unittest

from arraypeep import ast_nodes as ast
from arraypeep.codegen import LEVELS, CodegenError, CodeGenerator, generate, generate_code
from arraypeep.parser import parse
from arraypeep.quad import (
    OP_LOAD,
    OP_STORE,
    TAG_ADDR,
    TAG_BODY,
    Indexed,
    Quad,
    format_listing,
    well_formedness_errors,
)
from arraypeep.semantic import analyze
from arraypeep.symbol_table import ArrayPeepError

from test_codegen_scalar import GOLDEN, GOLDEN_NAMES, EXAMPLES


def gen(source: str, level: str):
    program = parse(source)
    return generate(program, analyze(program), level)


def rows(quads: list[Quad]) -> list[tuple]:
    return [(q.tag, q.op, q.arg1, q.arg2, q.result) for q in quads]


def eq1(base: int, bounds: list[tuple[int, int]], subs: tuple[int, ...]) -> int:
    """Independent Eq. 1: base + sum over k of (i_k - l_k) * d_(k+1) * ... * d_n."""
    total = base
    for k, (i, (lo, _)) in enumerate(zip(subs, bounds)):
        prod = 1
        for lo2, up2 in bounds[k + 1:]:
            prod *= up2 - lo2 + 1
        total += (i - lo) * prod
    return total


def execute(quads: list[Quad]) -> tuple[dict, list[tuple[str, int]], dict[int, int]]:
    """Run a listing: (scalars, address trace, memory). Memory cell ``a`` starts as ``a``."""
    match, stack = {}, []
    for pc, q in enumerate(quads):
        if q.op == "FOR":
            stack.append(pc)
        elif q.op == "ENDFOR":
            match[stack.pop()] = pc
    for_of = {end: start for start, end in match.items()}
    env: dict[str, int] = {}
    mem: dict[int, int] = {}
    trace: list[tuple[str, int]] = []

    def val(x):
        return x if isinstance(x, int) else env.get(x, 0)

    def addr(cell: Indexed) -> int:
        return cell.base + val(cell.index)

    pc = 0
    while pc < len(quads):
        q = quads[pc]
        if q.op == "FOR":
            env[q.arg1] = q.arg2
            if q.arg2 > q.result:
                pc = match[pc]
        elif q.op == "ENDFOR":
            start = quads[for_of[pc]]
            env[q.arg1] += 1
            if env[q.arg1] <= start.result:
                pc = for_of[pc]
        elif q.op in "+-*" and len(q.op) == 1:
            a, b = val(q.arg1), val(q.arg2)
            env[q.result] = a + b if q.op == "+" else a - b if q.op == "-" else a * b
        elif q.op == ":=":
            env[q.result] = val(q.arg1)
        elif q.op == OP_LOAD:
            e = addr(q.arg1)
            trace.append(("R", e))
            env[q.result] = mem.get(e, e)
        elif q.op == OP_STORE:
            e = addr(q.result)
            trace.append(("W", e))
            mem[e] = val(q.arg1)
        pc += 1
    return env, trace, mem


def first_address(decl: str, ref: str, level: str) -> int:
    """Address read by ``s = <ref>;`` (constant subscripts)."""
    _, trace, _ = execute(gen(f"{decl}\ns = {ref};", level).quads)
    assert len(trace) == 1 and trace[0][0] == "R"
    return trace[0][1]


# (declaration, bounds, base, three hand-checked subscript lists with their Eq. 1 addresses)
ARRAYS = [
    ("array V[5:9] at 200;", [(5, 9)], 200, [((5,), 200), ((7,), 202), ((9,), 204)]),
    ("array V[-3:3] at 50;", [(-3, 3)], 50, [((-3,), 50), ((0,), 53), ((3,), 56)]),
    ("array A[1:10, 1:20] at 1000;", [(1, 10), (1, 20)], 1000,
     [((1, 1), 1000), ((2, 3), 1022), ((10, 20), 1199)]),
    ("array B[0:2, -1:1] at 7;", [(0, 2), (-1, 1)], 7, [((0, -1), 7), ((1, 0), 11), ((2, 1), 15)]),
    ("array C[1:2, 0:2, 3:6] at 100;", [(1, 2), (0, 2), (3, 6)],
     100, [((1, 0, 3), 100), ((1, 2, 5), 110), ((2, 2, 6), 123)]),
    ("array D[2:4, 1:3, 0:1, 1:2] at 0;", [(2, 4), (1, 3), (0, 1), (1, 2)], 0,
     [((2, 1, 0, 1), 0), ((3, 2, 1, 1), 18), ((4, 3, 1, 2), 35)]),
]


class HandCheckedAddressTests(unittest.TestCase):
    def test_roadmap_example_a_2_3(self) -> None:
        """A[1:10,1:20] at 1000: CONSPART = 979, VARPART(2,3) = 2*20+3 = 43, address 1022."""
        decl = "array A[1:10, 1:20] at 1000;"
        l1 = gen(decl + "\ns = A[2, 3];", "L1")
        self.assertEqual(
            rows(l1.quads),
            [(TAG_ADDR, "*", 2, 20, "T1"), (TAG_ADDR, "+", "T1", 3, "T2"),
             (TAG_BODY, OP_LOAD, Indexed(979, "T2"), None, "T3"), (TAG_BODY, ":=", "T3", None, "s")],
        )
        self.assertEqual(execute(l1.quads)[0]["T2"], 43)
        self.assertEqual(979 + 43, 1022)
        self.assertEqual(execute(l1.quads)[1], [("R", 1022)])
        l0 = gen(decl + "\ns = A[2, 3];", "L0")
        self.assertEqual(
            rows(l0.quads)[:7],
            [(TAG_ADDR, "-", 2, 1, "T1"), (TAG_ADDR, "*", "T1", 20, "T2"), (TAG_ADDR, "-", 3, 1, "T3"),
             (TAG_ADDR, "+", "T2", "T3", "T4"), (TAG_ADDR, "+", "T4", 1000, "T5"),
             (TAG_BODY, OP_LOAD, Indexed(0, "T5"), None, "T6"), (TAG_BODY, ":=", "T6", None, "s")],
        )
        self.assertEqual(execute(l0.quads)[1], [("R", 1022)])

    def test_three_points_per_array_at_both_levels(self) -> None:
        for decl, bounds, base, points in ARRAYS:
            name = decl.split()[1].split("[")[0]
            for subs, expected in points:
                self.assertEqual(eq1(base, bounds, subs), expected)  # the hand value is right
                ref = f"{name}[{', '.join(str(s) for s in subs)}]"
                for level in LEVELS:
                    with self.subTest(array=name, subs=subs, level=level):
                        self.assertEqual(first_address(decl, ref, level), expected)

    def test_subscript_expressions_with_unary_minus(self) -> None:
        decl = "array A[1:10, 1:20] at 1000;"
        for level in LEVELS:
            self.assertEqual(first_address(decl, "A[3 - 1, -(0 - 4)]", level), eq1(1000, [(1, 10), (1, 20)], (2, 4)))

    def test_every_element_of_small_arrays_matches_eq1_at_both_levels(self) -> None:
        cases = [("array A[0:2] at 9;", [(0, 2)], 9), ("array B[1:3, -1:1] at 40;", [(1, 3), (-1, 1)], 40),
                 ("array C[1:2, 1:2, 1:3] at 5;", [(1, 2), (1, 2), (1, 3)], 5)]
        for decl, bounds, base in cases:
            name = decl.split()[1][0]
            loops = "".join(f"for i{k} = {lo} to {up} do " for k, (lo, up) in enumerate(bounds))
            idx = ", ".join(f"i{k}" for k in range(len(bounds)))
            src = f"{decl}\n{loops}s = {name}[{idx}]; {'end ' * len(bounds)}"
            expected = [("R", eq1(base, bounds, s)) for s in itertools.product(*(range(lo, up + 1) for lo, up in bounds))]
            for level in LEVELS:
                with self.subTest(array=name, level=level):
                    self.assertEqual(execute(gen(src, level).quads)[1], expected)

    def test_random_subscripts_agree_across_levels_and_with_eq1(self) -> None:
        rng = random.Random(5)
        for _ in range(60):
            n = rng.randint(1, 4)
            bounds = [(lo := rng.randint(-4, 4), lo + rng.randint(0, 5)) for _ in range(n)]
            base = rng.randint(0, 500)
            name = "Q"
            decl = f"array Q[{', '.join(f'{lo}:{up}' for lo, up in bounds)}] at {base};"
            subs = tuple(rng.randint(lo, up) for lo, up in bounds)
            ref = f"Q[{', '.join(str(s) if s >= 0 else f'(0 - {-s})' for s in subs)}]"
            want = eq1(base, bounds, subs)
            for level in LEVELS:
                self.assertEqual(first_address(decl, ref, level), want, (decl, ref, level))


class ReadWriteTests(unittest.TestCase):
    def test_write_form_and_value_flow(self) -> None:
        g = gen("array A[1:10] at 100;\nA[3] = 5;", "L1")
        self.assertEqual(rows(g.quads), [(TAG_BODY, OP_STORE, 5, None, Indexed(99, 3))])
        self.assertEqual(execute(g.quads)[1:], ([("W", 102)], {102: 5}))
        g0 = gen("array A[1:10] at 100;\nA[3] = 5;", "L0")
        self.assertEqual(
            rows(g0.quads),
            [(TAG_ADDR, "-", 3, 1, "T1"), (TAG_ADDR, "+", "T1", 100, "T2"), (TAG_BODY, OP_STORE, 5, None, Indexed(0, "T2"))],
        )

    def test_write_stores_the_translated_right_side(self) -> None:
        for level in LEVELS:
            env, trace, mem = execute(gen("array A[1:3] at 10;\nfor i = 1 to 3 do A[i] = i * i; end", level).quads)
            self.assertEqual(mem, {10: 1, 11: 4, 12: 9})
            self.assertEqual(trace, [("W", 10), ("W", 11), ("W", 12)])

    def test_reads_happen_before_the_write(self) -> None:
        """language_spec.md 4.4: Z[i,j] = Z[i,j] + X[i,k] * Y[k,j] reads Z, X, Y, then writes Z."""
        src = ("array Z[1:2, 1:2] at 0;\narray X[1:2, 1:2] at 10;\narray Y[1:2, 1:2] at 20;\n"
               "i = 2;\n")  # i is only a scalar here; the real check is with constants below
        src = src.replace("i = 2;\n", "") + "Z[2, 1] = Z[2, 1] + X[2, 2] * Y[2, 1];"
        for level in LEVELS:
            g = gen(src, level)
            self.assertEqual(execute(g.quads)[1], [("R", 2), ("R", 13), ("R", 22), ("W", 2)])
            self.assertEqual([(d.array, d.is_write) for d in g.accesses],
                             [("Z", False), ("X", False), ("Y", False), ("Z", True)])

    def test_read_result_is_a_fresh_temporary_used_by_the_expression(self) -> None:
        g = gen("array A[1:3] at 0;\ns = A[1] + A[2];", "L1")
        self.assertEqual(
            rows(g.quads),
            [(TAG_BODY, OP_LOAD, Indexed(-1, 1), None, "T1"), (TAG_BODY, OP_LOAD, Indexed(-1, 2), None, "T2"),
             (TAG_BODY, "+", "T1", "T2", "T3"), (TAG_BODY, ":=", "T3", None, "s")],
        )

    def test_indexed_operands_print_in_the_listing(self) -> None:
        text = format_listing(gen("array A[1:10, 1:20] at 1000;\ns = A[2, 3];", "L1").quads)
        self.assertIn("979[T2]", text)
        self.assertIn("=[ ]", text)

    def test_listing_is_well_formed_at_both_levels(self) -> None:
        src = ("array A[1:5, 1:5] at 0;\narray V[0:9] at 100;\n"
               "for i = 1 to 5 do for j = 1 to 5 do A[i, j] = V[i + j] + A[j, i]; end end")
        for level in LEVELS:
            self.assertEqual(well_formedness_errors(gen(src, level).quads), [])

    def test_levels_agree_on_the_final_memory_and_trace(self) -> None:
        src = ("array A[1:4, 1:3] at 20;\narray V[0:4] at 100;\n"
               "for i = 1 to 4 do for j = 1 to 3 do A[i, j] = A[i, j] + V[i] * j; V[0] = V[0] + 1; end end")
        out = {level: execute(gen(src, level).quads) for level in LEVELS}
        self.assertEqual(out["L0"][1:], out["L1"][1:])
        self.assertEqual(len(out["L0"][1]), 4 * 3 * 5)


class OneDimensionTests(unittest.TestCase):
    def test_l1_one_dimension_has_no_arithmetic(self) -> None:
        g = gen("array A[1:10] at 100;\nfor i = 1 to 10 do s = A[i]; end", "L1")
        self.assertEqual(g.quads[1], Quad(OP_LOAD, Indexed(99, "i"), None, "T1", TAG_BODY))
        self.assertFalse([q for q in g.quads if q.tag == TAG_ADDR])
        d = g.accesses[0]
        self.assertEqual((d.first, d.last, d.access), (2, 1, 2))  # empty window just before the access
        self.assertEqual(list(d.window), [])

    def test_l1_one_dimension_with_an_expression_subscript(self) -> None:
        g = gen("array A[1:10] at 100;\nfor i = 1 to 4 do s = A[2 * i + 1]; end", "L1")
        self.assertEqual([(q.op, q.tag) for q in g.quads[1:4]], [("*", TAG_ADDR), ("+", TAG_ADDR), (OP_LOAD, TAG_BODY)])
        self.assertEqual(g.quads[3].arg1, Indexed(99, "T2"))
        self.assertEqual((g.accesses[0].first, g.accesses[0].last, g.accesses[0].access), (2, 3, 4))

    def test_l1_constant_subscript(self) -> None:
        g = gen("array A[1:10] at 100;\ns = A[4];", "L1")
        self.assertEqual(g.quads[0].arg1, Indexed(99, 4))

    def test_l0_one_dimension_still_subtracts_and_adds(self) -> None:
        g = gen("array A[0:9] at 100;\nfor i = 0 to 9 do s = A[i]; end", "L0")
        self.assertEqual(rows(g.quads)[1:4], [(TAG_ADDR, "-", "i", 0, "T1"), (TAG_ADDR, "+", "T1", 100, "T2"),
                                              (TAG_BODY, OP_LOAD, Indexed(0, "T2"), None, "T3")])


class VarpartChainTests(unittest.TestCase):
    @staticmethod
    def count(g, op: str) -> int:
        return sum(1 for q in g.quads if q.tag == TAG_ADDR and q.op == op)

    def test_two_dimensional_chain_has_one_multiplication_and_one_addition(self) -> None:
        g = gen("array A[1:10, 1:20] at 1000;\nfor i = 1 to 2 do for j = 1 to 2 do s = A[i, j]; end end", "L1")
        self.assertEqual((self.count(g, "*"), self.count(g, "+"), self.count(g, "-")), (1, 1, 0))
        self.assertEqual(rows(g.quads)[2:4], [(TAG_ADDR, "*", "i", 20, "T1"), (TAG_ADDR, "+", "T1", "j", "T2")])
        self.assertEqual(g.quads[4].arg1, Indexed(979, "T2"))

    def test_chain_length_is_n_minus_one_multiply_adds(self) -> None:
        for n in (1, 2, 3, 4):
            bounds = ", ".join("1:3" for _ in range(n))
            idx = ", ".join("1" for _ in range(n))
            g = gen(f"array A[{bounds}] at 0;\ns = A[{idx}];", "L1")
            self.assertEqual((self.count(g, "*"), self.count(g, "+")), (n - 1, n - 1))

    def test_three_dimensional_chain_uses_the_inner_sizes_in_order(self) -> None:
        g = gen("array C[1:2, 0:2, 3:6] at 100;\nfor i = 1 to 2 do for j = 0 to 2 do for k = 3 to 6 do s = C[i, j, k]; end end end", "L1")
        window = [(q.op, q.arg1, q.arg2) for q in g.quads if q.tag == TAG_ADDR]
        self.assertEqual(window, [("*", "i", 3), ("+", "T1", "j"), ("*", "T2", 4), ("+", "T3", "k")])
        # CONSPART = 100 - ((1*3 + 0)*4 + 3) = 85
        self.assertEqual([q.arg1 for q in g.quads if q.op == OP_LOAD], [Indexed(85, "T4")])

    def test_l1_emits_the_subscript_code_before_the_multiplication(self) -> None:
        """quad_spec.md 7.2: A[2*i+1, j-1]; the L1 listing is exact."""
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = A[2 * i + 1, j - 1]; end end"
        g = gen(src, "L1")
        self.assertEqual(
            [(q.op, q.arg1, q.arg2, q.result) for q in g.quads[2:8]],
            [("*", 2, "i", "T1"), ("+", "T1", 1, "T2"), ("-", "j", 1, "T3"), ("*", "T2", 20, "T4"),
             ("+", "T4", "T3", "T5"), (OP_LOAD, Indexed(979, "T5"), None, "T6")],
        )

    def test_l0_listing_of_the_second_example_is_exact(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = A[2 * i + 1, j - 1]; end end"
        g = gen(src, "L0")
        self.assertEqual(
            [(q.op, q.arg1, q.arg2, q.result) for q in g.quads[2:11]],
            [("*", 2, "i", "T1"), ("+", "T1", 1, "T2"), ("-", "T2", 1, "T3"), ("-", "j", 1, "T4"),
             ("*", "T3", 20, "T5"), ("-", "T4", 1, "T6"), ("+", "T5", "T6", "T7"), ("+", "T7", 1000, "T8"),
             (OP_LOAD, Indexed(0, "T8"), None, "T9")],
        )
        # per-execution counts of quad_spec.md 7.2: L0 2 mult, 6 add/sub; L1 2 mult, 3 add/sub
        def counts(level):
            w = [q for q in gen(src, level).quads if q.tag == TAG_ADDR]
            return sum(q.op == "*" for q in w), sum(q.op in "+-" for q in w)
        self.assertEqual((counts("L0"), counts("L1")), ((2, 6), (2, 3)))

    def test_l0_uses_one_subtraction_per_dimension_and_the_base_addition(self) -> None:
        for n in (1, 2, 3):
            bounds = ", ".join("0:4" for _ in range(n))
            idx = ", ".join("1" for _ in range(n))
            q = gen(f"array A[{bounds}] at 7;\ns = A[{idx}];", "L0").quads
            ops = [x.op for x in q if x.tag == TAG_ADDR]
            self.assertEqual((ops.count("-"), ops.count("*"), ops.count("+")), (n, n - 1, n))  # n - 1 chain adds + base
            self.assertEqual([x for x in q if x.tag == TAG_ADDR][-1].arg2, 7)  # the base address


class DimensionMismatchTests(unittest.TestCase):
    @staticmethod
    def ref_program(count: int) -> ast.Program:
        decl = ast.ArrayDecl("A", ((1, 10), (1, 20)), 1000)
        ref = ast.ArrayRef("A", tuple(ast.Number(1) for _ in range(count)), line=3)
        return ast.Program((decl, ast.Assign(ast.ScalarRef("s"), ref)))

    def test_wrong_subscript_counts_are_rejected_at_both_levels(self) -> None:
        for count in (1, 3, 4):
            for level in LEVELS:
                with self.subTest(count=count, level=level):
                    with self.assertRaises(CodegenError) as ctx:
                        generate_code(self.ref_program(count), level=level)
                    self.assertIn("has 2 dimensions", ctx.exception.message)
                    self.assertIn(f"{count} subscript(s) given", ctx.exception.message)
                    self.assertEqual(ctx.exception.line, 3)
                    self.assertIsInstance(ctx.exception, ArrayPeepError)

    def test_write_with_wrong_count_is_rejected(self) -> None:
        decl = ast.ArrayDecl("A", ((1, 10), (1, 20)), 1000)
        bad = ast.Assign(ast.ArrayRef("A", (ast.Number(1),), line=2), ast.Number(0))
        for level in LEVELS:
            with self.assertRaises(CodegenError):
                generate_code(ast.Program((decl, bad)), level=level)

    def test_undeclared_array_is_rejected(self) -> None:
        program = ast.Program((ast.Assign(ast.ScalarRef("s"), ast.ArrayRef("Z", (ast.Number(1),), line=1)),))
        for level in LEVELS:
            with self.assertRaises(CodegenError):
                generate_code(program, level=level)

    def test_unknown_level_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CodeGenerator(level="L2")
        self.assertEqual(LEVELS, ("L0", "L1"))

    def test_the_default_level_is_l1(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\ns = A[2, 3];"
        self.assertEqual(generate_code(parse(src)), gen(src, "L1").quads)


SUBSCRIPT_PROGRAMS = [
    "array A[1:10, 1:20] at 1000;\nfor i = 1 to 3 do for j = 1 to 3 do A[i, j] = A[i, j] + 1; end end",
    "array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 9 do s = V[W[i]]; end",
    "array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 4 do V[W[i + 1] - 100] = V[i] + W[W[i] - 100]; end",
    "array C[1:2, 1:3, 1:4] at 50;\nfor i = 1 to 2 do for j = 1 to 3 do for k = 1 to 4 do C[i, j, k] = C[i, j, k] * 2; end end end",
]


class DescriptorTests(unittest.TestCase):
    def check(self, source: str, level: str):
        g = gen(source, level)
        for n, d in enumerate(g.accesses):
            with self.subTest(source=source[:30], level=level, access=n):
                self.assertEqual(d.last + 1, d.access)
                self.assertEqual(g.quads[d.access - 1].op, OP_STORE if d.is_write else OP_LOAD)
                self.assertEqual(g.quads[d.access - 1].tag, TAG_BODY)
                self.assertTrue(1 <= d.first <= d.access)
        return g

    def test_windows_end_immediately_before_the_access(self) -> None:
        for src, level in itertools.product(SUBSCRIPT_PROGRAMS, LEVELS):
            self.check(src, level)

    def test_window_holds_exactly_the_addr_quads_of_a_simple_access(self) -> None:
        src = SUBSCRIPT_PROGRAMS[0]
        for level in LEVELS:
            g = self.check(src, level)
            for d in g.accesses:
                window = [g.quads[p - 1] for p in d.window]
                self.assertTrue(window and all(q.tag == TAG_ADDR for q in window))
            addr_positions = {p for p, q in enumerate(g.quads, 1) if q.tag == TAG_ADDR}
            covered = {p for d in g.accesses for p in d.window}
            self.assertEqual(covered, addr_positions)  # every ADDR quad belongs to exactly one window

    def test_windows_of_simple_accesses_do_not_overlap(self) -> None:
        g = self.check(SUBSCRIPT_PROGRAMS[3], "L1")
        seen: set[int] = set()
        for d in g.accesses:
            self.assertFalse(seen & set(d.window))
            seen |= set(d.window)

    def test_exact_positions_for_the_roadmap_example(self) -> None:
        src = "array A[1:10, 1:20] at 1000;\nfor i = 1 to 10 do for j = 1 to 20 do s = s + A[i, j]; end end"
        l0, l1 = (gen(src, lv).accesses[0] for lv in LEVELS)
        self.assertEqual((l0.first, l0.last, l0.access), (3, 7, 8))   # FOR FOR, 5 address quads, =[ ]
        self.assertEqual((l1.first, l1.last, l1.access), (3, 4, 5))   # FOR FOR, 2 address quads, =[ ]
        self.assertEqual((l1.array, l1.is_write), ("A", False))
        self.assertEqual([type(s).__name__ for s in l1.subscripts], ["ScalarRef", "ScalarRef"])
        self.assertEqual([s.name for s in l1.subscripts], ["i", "j"])

    def test_descriptor_records_the_subscript_expressions(self) -> None:
        g = gen("array A[1:10, 1:20] at 1000;\nfor i = 1 to 4 do for j = 2 to 20 do s = A[2 * i + 1, j - 1]; end end", "L1")
        d = g.accesses[0]
        self.assertEqual(ast.format_expr(d.subscripts[0]), "2 * i + 1")
        self.assertEqual(ast.format_expr(d.subscripts[1]), "j - 1")
        self.assertEqual((d.first, d.last, d.access), (3, 7, 8))

    def test_descriptors_are_in_access_order_with_distinct_access_positions(self) -> None:
        for level in LEVELS:
            g = self.check(SUBSCRIPT_PROGRAMS[2], level)
            positions = [d.access for d in g.accesses]
            self.assertEqual(positions, sorted(set(positions)))
            self.assertEqual(len(positions), sum(q.op in (OP_LOAD, OP_STORE) for q in g.quads))

    def test_every_addr_quad_lies_in_some_window_and_only_addr_or_nested_accesses_do(self) -> None:
        for src, level in itertools.product(SUBSCRIPT_PROGRAMS, LEVELS):
            g = gen(src, level)
            covered: set[int] = set()
            for d in g.accesses:
                covered |= set(d.window)
            addr = {p for p, q in enumerate(g.quads, 1) if q.tag == TAG_ADDR}
            self.assertLessEqual(addr, covered)
            for p in covered - addr:  # a BODY quad inside a window is a nested array read
                self.assertEqual(g.quads[p - 1].op, OP_LOAD)

    def test_nested_access_lies_inside_the_outer_window(self) -> None:
        for level in LEVELS:
            g = gen(SUBSCRIPT_PROGRAMS[1], level)
            inner, outer = g.accesses
            self.assertEqual((inner.array, outer.array), ("W", "V"))
            self.assertTrue(outer.first <= inner.first and inner.access <= outer.last)
            self.assertEqual(g.quads[inner.access - 1].tag, TAG_BODY)
            self.assertEqual(outer.last + 1, outer.access)

    def test_nested_subscript_addresses_are_correct(self) -> None:
        # W[i] initially holds its own address (100 + i), so V[W[i]] reads address 100 + i -> outside V;
        # use W[i] - 100 to land inside V and check the trace pairs (R W[i], R V[i]).
        src = "array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 3 do s = V[W[i] - 100]; end"
        for level in LEVELS:
            self.assertEqual(execute(gen(src, level).quads)[1],
                             [x for i in range(4) for x in (("R", 100 + i), ("R", i))])

    def test_empty_window_convention(self) -> None:
        d = gen("array A[1:3] at 0;\nfor i = 1 to 3 do s = A[i]; end", "L1").accesses[0]
        self.assertEqual((d.first, d.last, d.access), (d.access, d.access - 1, d.access))


class AddrTagTests(unittest.TestCase):
    def test_access_quads_are_body_and_address_arithmetic_is_addr(self) -> None:
        for src, level in itertools.product(SUBSCRIPT_PROGRAMS, LEVELS):
            for q in gen(src, level).quads:
                if q.op in (OP_LOAD, OP_STORE, "FOR", "ENDFOR", ":="):
                    self.assertEqual(q.tag, TAG_BODY, q)

    def test_subscript_arithmetic_is_addr_and_user_arithmetic_is_body(self) -> None:
        g = gen("array A[1:10] at 0;\nfor i = 1 to 4 do s = A[i + 1] * 2 + s; end", "L1")
        tags = [(q.op, q.tag) for q in g.quads[1:-1]]
        self.assertEqual(tags, [("+", TAG_ADDR), (OP_LOAD, TAG_BODY), ("*", TAG_BODY), ("+", TAG_BODY), (":=", TAG_BODY)])

    def test_scalar_programs_have_no_addr_quads_at_either_level(self) -> None:
        for level in LEVELS:
            self.assertFalse([q for q in gen("for i = 1 to 3 do s = s + i * 2; end", level).quads if q.tag == TAG_ADDR])

    def test_addr_count_is_the_sum_over_windows_for_a_simple_program(self) -> None:
        for level, per_access in (("L0", 5), ("L1", 2)):
            g = gen(SUBSCRIPT_PROGRAMS[0], level)
            self.assertEqual(sum(q.tag == TAG_ADDR for q in g.quads), per_access * len(g.accesses))


class LoopTableTests(unittest.TestCase):
    def test_nested_and_sequential_loops(self) -> None:
        src = ("array A[1:3] at 0;\n"
               "for i = 1 to 3 do\n  for j = 2 to 4 do\n    for k = 0 to 1 do s = s + 1; end\n  end\n  s = A[i];\nend\n"
               "for m = 5 to 1 do s = 0; end")
        g = gen(src, "L1")
        self.assertEqual(
            [(e.var, e.lo, e.hi, e.depth, e.for_pos, e.endfor_pos) for e in g.loops],
            [("i", 1, 3, 1, 1, 10), ("j", 2, 4, 2, 2, 7), ("k", 0, 1, 3, 3, 6), ("m", 5, 1, 1, 11, 13)],
        )

    def test_positions_point_at_the_matching_markers(self) -> None:
        for src, level in itertools.product(SUBSCRIPT_PROGRAMS + [
                "for i = 1 to 2 do for j = 1 to 2 do s = s + 1; end for k = 1 to 2 do s = s + 2; end end"], LEVELS):
            g = gen(src, level)
            for e in g.loops:
                f, en = g.quads[e.for_pos - 1], g.quads[e.endfor_pos - 1]
                self.assertEqual((f.op, f.arg1, f.arg2, f.result), ("FOR", e.var, e.lo, e.hi))
                self.assertEqual((en.op, en.arg1), ("ENDFOR", e.var))
                self.assertLess(e.for_pos, e.endfor_pos)

    def test_table_is_in_for_order_and_depth_matches_enclosure(self) -> None:
        g = gen("for i = 1 to 2 do for j = 1 to 2 do s = 1; end for k = 1 to 2 do s = 2; end end for m = 1 to 2 do s = 3; end", "L0")
        self.assertEqual([(e.var, e.depth) for e in g.loops], [("i", 1), ("j", 2), ("k", 2), ("m", 1)])
        self.assertEqual([e.for_pos for e in g.loops], sorted(e.for_pos for e in g.loops))
        for outer, inner in itertools.permutations(g.loops, 2):
            if outer.for_pos < inner.for_pos < outer.endfor_pos:
                self.assertGreater(inner.depth, outer.depth)
                self.assertLess(inner.endfor_pos, outer.endfor_pos)

    def test_positions_depend_on_the_level_but_bounds_do_not(self) -> None:
        src = SUBSCRIPT_PROGRAMS[0]
        l0, l1 = gen(src, "L0").loops, gen(src, "L1").loops
        self.assertEqual([(e.var, e.lo, e.hi, e.depth) for e in l0], [(e.var, e.lo, e.hi, e.depth) for e in l1])
        self.assertGreater(l0[0].endfor_pos, l1[0].endfor_pos)

    def test_no_loops_gives_an_empty_table(self) -> None:
        self.assertEqual(gen("array A[1:2] at 0;\nA[1] = 2;", "L1").loops, [])

    def test_zero_trip_loop_is_recorded(self) -> None:
        self.assertEqual([(e.var, e.lo, e.hi) for e in gen("for i = 3 to 1 do s = 1; end", "L1").loops], [("i", 3, 1)])


class QuadIndexedOperandTests(unittest.TestCase):
    def test_indexed_operand_is_only_allowed_in_the_access_opcodes(self) -> None:
        cell = Indexed(5, "T1")
        Quad(OP_LOAD, cell, None, "T2")
        Quad(OP_STORE, "T2", None, cell)
        for bad in (lambda: Quad("+", cell, 1, "T2"), lambda: Quad(":=", "T1", None, cell),
                    lambda: Quad(OP_LOAD, "T1", cell, "T2"), lambda: Quad(OP_STORE, cell, None, "T2")):
            with self.assertRaises(ValueError):
                bad()
        self.assertEqual(str(cell), "5[T1]")
        with self.assertRaises(ValueError):
            Indexed(True, 1)

    def test_well_formedness_checks_indexed_operands(self) -> None:
        bad = [Quad(OP_LOAD, Indexed(5, "T9"), None, "T1")]
        self.assertTrue(any("T9" in e for e in well_formedness_errors(bad)))
        bad = [Quad(OP_STORE, "T7", None, Indexed(5, 0))]
        self.assertTrue(any("T7" in e for e in well_formedness_errors(bad)))
        bad = [Quad(OP_LOAD, Indexed(5, 0), None, "s")]
        self.assertTrue(well_formedness_errors(bad))
        self.assertEqual(well_formedness_errors([Quad(OP_LOAD, Indexed(5, 0), None, "T1")]), [])


class Phase4PreservedTests(unittest.TestCase):
    def test_scalar_golden_outputs_at_both_levels(self) -> None:
        for name, level in itertools.product(GOLDEN_NAMES, LEVELS):
            with self.subTest(program=name, level=level):
                source = (GOLDEN / f"{name}.ml").read_text(encoding="utf-8")
                expected = (GOLDEN / f"{name}.quads").read_text(encoding="utf-8").splitlines()
                program = parse(source)
                actual = format_listing(generate_code(program, analyze(program), level)).splitlines()
                self.assertEqual(actual, expected)

    def test_example_programs_are_well_formed_and_levels_agree(self) -> None:
        for path in EXAMPLES:
            program = parse(path.read_text(encoding="utf-8"))
            table = analyze(program)
            runs = {}
            for level in LEVELS:
                g = generate(program, table, level)
                self.assertEqual(well_formedness_errors(g.quads), [], (path.name, level))
                runs[level] = execute(g.quads)
            with self.subTest(example=path.name):
                self.assertEqual(runs["L0"][1:], runs["L1"][1:])
                self.assertEqual({k: v for k, v in runs["L0"][0].items() if not k.startswith("T")},
                                 {k: v for k, v in runs["L1"][0].items() if not k.startswith("T")})


if __name__ == "__main__":
    unittest.main()
