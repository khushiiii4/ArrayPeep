"""Tests for arraypeep.precompute and the Phase 8 part of arraypeep.optimizer (L2 code).

Hand-calculated values come from the roadmap (pages 22-23, 35-36) and
docs/quad_spec.md 7.2; the product-form Eq. 1 and the tree evaluator in this
file are independent of the code under test.
"""
import itertools
import pathlib
import re
import subprocess
import sys
import unittest

from arraypeep import ast_nodes as ast
from arraypeep.codegen import generate
from arraypeep.interpreter import execute
from arraypeep.linear_analysis import LinearForm
from arraypeep.optimizer import (
    L2Program,
    analyze_accesses,
    emit_l2,
    format_l2_report,
    optimize_l2,
    validate_windows,
)
from arraypeep.parser import parse
from arraypeep.precompute import AccessPlan, make_plan
from arraypeep.quad import OP_LOAD, OP_STORE, TAG_ADDR, TAG_BODY, Indexed, Quad, well_formedness_errors
from arraypeep.semantic import analyze
from arraypeep.symbol_table import ArrayInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.ml"))


def build(source: str, base_level: str = "L1"):
    """(L1 code, symbol table, L2 program) of ``source``."""
    program = parse(source)
    table = analyze(program)
    code = generate(program, table, base_level)
    return code, table, optimize_l2(code, table)


def plans(source: str) -> list[AccessPlan]:
    return [e.plan for e in build(source)[2].entries]


def eq1(info: ArrayInfo, subs) -> int:
    """Independent product-form Eq. 1: a + SUM (i_k - l_k) * d_(k+1) * ... * d_n."""
    total = info.base
    for k, i in enumerate(subs):
        weight = 1
        for lo, up in zip(info.lowers[k + 1:], info.uppers[k + 1:]):
            weight *= up - lo + 1
        total += (i - info.lowers[k]) * weight
    return total


def direct(e: ast.Expr, env) -> int:
    if isinstance(e, ast.Number):
        return e.value
    if isinstance(e, ast.ScalarRef):
        return env[e.name]
    if isinstance(e, ast.UnaryMinus):
        return -direct(e.operand, env)
    a, b = direct(e.left, env), direct(e.right, env)
    return a + b if e.op == "+" else a - b if e.op == "-" else a * b


def run(code, table, level):
    return execute(code.quads, table.arrays, level)


def canon(quads) -> list[tuple]:
    """Quadruples with temporaries renamed by first appearance, so listings can be compared by shape."""
    names: dict[str, str] = {}

    def name(x):
        if isinstance(x, Indexed):
            return f"{x.base}[{name(x.index)}]"
        if isinstance(x, str) and re.fullmatch(r"T[0-9]+", x):
            return names.setdefault(x, f"t{len(names) + 1}")
        return x

    return [(q.tag, q.op, name(q.arg1), name(q.arg2), name(q.result)) for q in quads]


DECL_A = "array A[1:10, 1:20] at 1000;\n"


def a_access(subs: str, loops=(("i", 1, 10), ("j", 1, 20)), stmt="s = s + A[{}];") -> str:
    head = "".join(f"for {v} = {lo} to {hi} do " for v, lo, hi in loops)
    return DECL_A + head + stmt.format(subs) + " end" * len(loops)


# -- plans: Eq. 5 --------------------------------------------------------------------------------


class PlanTests(unittest.TestCase):
    def check(self, source, conspart, d0, k, ds):
        (p,) = [x for x in plans(source) if x is not None][:1]
        self.assertEqual((p.conspart, p.d0, p.k, p.ds), (conspart, d0, k, ds))
        return p

    def test_roadmap_plan_table(self) -> None:
        self.check(a_access("i, j"), 979, 0, 979, (("i", 20), ("j", 1)))
        self.check(a_access("2*i + 1, j - 1"), 979, 19, 998, (("i", 40), ("j", 1)))
        self.check(a_access("i, 5"), 979, 5, 984, (("i", 20), ("j", 0)))
        self.check(a_access("11 - i, j"), 979, 220, 1199, (("i", -20), ("j", 1)))
        self.check(DECL_A + "x = A[3, 4];", 979, 64, 1043, ())
        self.check("array M[0:3, 0:4, 0:5] at 0;\nfor i = 0 to 3 do for j = 0 to 4 do for k = 0 to 5 do s = s + M[i, j, k]; end end end",
                   0, 0, 0, (("i", 30), ("j", 6), ("k", 1)))

    def test_other_roadmap_test_cases(self) -> None:
        # T02, T06, T09, T10, T16, T17, T18 of roadmap Section 5.3
        self.check("array V[5:14] at 500;\nfor i = 5 to 14 do s = s + V[i]; end", 495, 0, 495, (("i", 1),))
        self.check("array N[-5:5, -3:3] at 200;\nfor i = -5 to 5 do for j = -3 to 3 do s = s + N[i, j]; end end",
                   238, 0, 238, (("i", 7), ("j", 1)))
        self.check("array S[1:8, 1:8] at 300;\nfor i = 1 to 8 do for j = 1 to 8 do s = s + S[j, i]; end end",
                   291, 0, 291, (("i", 1), ("j", 8)))
        self.check("array U[1:1, 1:5] at 50;\nfor i = 1 to 1 do for j = 1 to 5 do s = s + U[i, j]; end end",
                   44, 0, 44, (("i", 5), ("j", 1)))
        self.check("array H[0:1, 0:1, 0:1, 0:2] at 0;\nfor a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do s = s + H[a, b, c, d]; end end end end",
                   0, 0, 0, (("a", 12), ("b", 6), ("c", 3), ("d", 1)))
        self.check(a_access("11 - i, j"), 979, 220, 1199, (("i", -20), ("j", 1)))

    def test_three_arrays_of_t10(self) -> None:
        src = ("array C[1:4, 1:5] at 0;\narray A[1:4, 1:5] at 100;\narray B[1:4, 1:5] at 200;\n"
               "for i = 1 to 4 do for j = 1 to 5 do C[i, j] = A[i, j] + B[i, j]; end end")
        got = {e.report.array: (e.plan.k, [d for _, d in e.plan.ds]) for e in build(src)[2].entries}
        self.assertEqual(got, {"C": (-6, [5, 1]), "A": (94, [5, 1]), "B": (194, [5, 1])})

    def test_matrix_multiply_t11(self) -> None:
        src = (ROOT / "examples" / "matrix_multiply.ml").read_text(encoding="utf-8")
        by = {}
        for e in build(src)[2].entries:
            by.setdefault(e.report.array, []).append((e.plan.k, [d for _, d in e.plan.ds]))
        self.assertEqual(by["X"], [(-5, [4, 0, 1])])
        self.assertEqual(by["Y"], [(11, [0, 1, 4])])
        self.assertEqual(by["Z"], [(27, [4, 1]), (27, [4, 1, 0]), (27, [4, 1, 0])])  # the first is Z[i,j] = 0 outside k

    def test_roadmap_t04_hand_calculation(self) -> None:
        """K = 979 + 19 = 998 and 998 + 40*i + j; first address 1040, last 1178."""
        src = DECL_A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end"
        (p,) = plans(src)
        # D0 = 1 * 20 + (-1) * 1 = 19;  Di = 2 * 20 = 40;  Dj = 1 * 1 = 1
        self.assertEqual((p.d0, p.k, p.ds), (19, 998, (("i", 40), ("j", 1))))
        self.assertEqual(p.expr_text(), "998 + 40*i + j")
        self.assertEqual(p.address({"i": 1, "j": 2}), 1040)
        self.assertEqual(p.address({"i": 4, "j": 20}), 1178)
        code, table, l2 = build(src)
        trace = [a for _, a in run(l2.code, table, "L2").trace]
        self.assertEqual((trace[0], trace[-1], len(trace)), (1040, 1178, 76))
        self.assertEqual(trace, [998 + 40 * i + j for i in range(1, 5) for j in range(2, 21)])

    def test_expr_text(self) -> None:
        cases = [(AccessPlan("A", 0, 0, 5, ()), "5"), (AccessPlan("A", 0, 0, 5, (("i", 1),)), "5 + i"),
                 (AccessPlan("A", 0, 0, -4, (("i", -1), ("j", 0), ("k", 7))), "-4 - i + 7*k"),
                 (AccessPlan("A", 0, 0, 1199, (("i", -20), ("j", 1))), "1199 - 20*i + j")]
        for plan, text in cases:
            self.assertEqual(plan.expr_text(), text)

    def test_wrong_number_of_forms(self) -> None:
        info = ArrayInfo("A", [(1, 3), (1, 3)], 0)
        with self.assertRaises(ValueError):
            make_plan(info, [LinearForm(0, {})], ["i"])

    def test_missing_coefficients_are_zero(self) -> None:
        info = ArrayInfo("A", [(1, 3), (1, 3)], 0)
        p = make_plan(info, [LinearForm(2, {}), LinearForm(0, {"j": 1})], ["i", "j"])
        self.assertEqual(p.ds, (("i", 0), ("j", 1)))
        self.assertEqual(p.terms, (("j", 1),))
        self.assertEqual(p.k, info.conspart + 2 * 3)


# -- brute force against Eq. 1 ---------------------------------------------------------------------------


class BruteForceTests(unittest.TestCase):
    PROGRAMS = [
        a_access("i, j"),
        a_access("2*i + 1, j - 1", (("i", 1, 4), ("j", 2, 20))),
        a_access("i, 5"),
        a_access("11 - i, j"),
        a_access("(i + 1) * 4 - 3 * i, j + j", (("i", 1, 4), ("j", 1, 10))),
        DECL_A + "x = A[3, 4];",
        "array V[5:14] at 500;\nfor i = 5 to 14 do V[i] = V[i] + i; end",
        "array H[0:1, 0:1, 0:1, 0:2] at 7;\nfor a = 0 to 1 do for b = 0 to 1 do for c = 0 to 1 do for d = 0 to 2 do H[a, b, c, d] = a + d; end end end end",
        "array S[1:8, 1:8] at 300;\nfor i = 1 to 8 do for j = 1 to 8 do s = s + S[j, i]; end end",
    ] + [p.read_text(encoding="utf-8") for p in EXAMPLES]

    def test_plan_address_equals_eq1_for_every_loop_value(self) -> None:
        checked = 0
        for src in self.PROGRAMS:
            code, table, l2 = build(src)
            for e in l2.entries:
                d = code.accesses[e.report.index]
                info = table.get_array(d.array)
                ranges = [range(l.lo, l.hi + 1) for l in e.report.loops]
                for values in itertools.product(*ranges):
                    env = {l.var: v for l, v in zip(e.report.loops, values)}
                    expected = eq1(info, [direct(s, env) for s in d.subscripts])
                    self.assertEqual(e.plan.address(env), expected, (src[:50], env))
                    checked += 1
        self.assertGreater(checked, 1000)

    def test_l2_listing_reproduces_the_l1_trace_exactly(self) -> None:
        for src in self.PROGRAMS:
            code, table, l2 = build(src)
            with self.subTest(src=src[:45]):
                a, b = run(code, table, "L1"), run(l2.code, table, "L2")
                self.assertEqual((a.scalars, a.memory, a.trace), (b.scalars, b.memory, b.trace))


# -- emission -------------------------------------------------------------------------------------


class EmitTests(unittest.TestCase):
    @staticmethod
    def emit(ds, k=100):
        n = iter(range(1, 100))
        plan = AccessPlan("A", 0, 0, k, tuple(ds))
        quads, place = emit_l2(plan, lambda: f"T{next(n)}")
        return [(q.tag, q.op, q.arg1, q.arg2, q.result) for q in quads], place

    def test_no_term_gives_a_constant_address(self) -> None:
        self.assertEqual(self.emit([]), ([], 0))
        self.assertEqual(self.emit([("i", 0), ("j", 0)]), ([], 0))

    def test_a_single_unit_term_needs_no_arithmetic(self) -> None:
        self.assertEqual(self.emit([("i", 1)]), ([], "i"))
        self.assertEqual(self.emit([("i", 0), ("j", 1)]), ([], "j"))

    def test_one_multiplication_per_coefficient_other_than_one(self) -> None:
        self.assertEqual(self.emit([("i", 5)]), ([(TAG_ADDR, "*", "i", 5, "T1")], "T1"))
        self.assertEqual(self.emit([("i", -1)]), ([(TAG_ADDR, "*", "i", -1, "T1")], "T1"))
        self.assertEqual(self.emit([("i", -20)]), ([(TAG_ADDR, "*", "i", -20, "T1")], "T1"))

    def test_terms_are_added_left_to_right_outermost_first(self) -> None:
        self.assertEqual(self.emit([("i", 40), ("j", 1)]),
                         ([(TAG_ADDR, "*", "i", 40, "T1"), (TAG_ADDR, "+", "T1", "j", "T2")], "T2"))
        self.assertEqual(self.emit([("i", 1), ("j", 20)]),
                         ([(TAG_ADDR, "*", "j", 20, "T1"), (TAG_ADDR, "+", "i", "T1", "T2")], "T2"))
        self.assertEqual(
            self.emit([("i", 3), ("j", 0), ("k", -2), ("m", 1)]),
            ([(TAG_ADDR, "*", "i", 3, "T1"), (TAG_ADDR, "*", "k", -2, "T2"), (TAG_ADDR, "+", "T1", "T2", "T3"),
              (TAG_ADDR, "+", "T3", "m", "T4")], "T4"),
        )

    def test_every_emitted_quadruple_is_addr(self) -> None:
        quads, _ = self.emit([("i", 3), ("j", 4), ("k", 5)])
        self.assertTrue(all(t == TAG_ADDR for t, *_ in quads))


# -- L2 listings ----------------------------------------------------------------------------------


class ListingTests(unittest.TestCase):
    def test_roadmap_listing_a_i_j(self) -> None:
        # quad_spec.md 7.1: L2 equals L1 for A[i, j]
        _, _, l2 = build(a_access("i, j"))
        shape = canon(l2.code.quads)[2:5]
        self.assertEqual(shape, [(TAG_ADDR, "*", "i", 20, "t1"), (TAG_ADDR, "+", "t1", "j", "t2"),
                                 (TAG_BODY, OP_LOAD, "979[t2]", None, "t3")])

    def test_roadmap_listing_t04(self) -> None:
        src = DECL_A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end"
        code, _, l2 = build(src)
        self.assertEqual(len(code.quads), 12)
        self.assertEqual(canon(l2.code.quads)[2:5], [(TAG_ADDR, "*", "i", 40, "t1"), (TAG_ADDR, "+", "t1", "j", "t2"),
                                                     (TAG_BODY, OP_LOAD, "998[t2]", None, "t3")])
        self.assertEqual(len(l2.code.quads), len(code.quads) - 3)  # 5 address quadruples became 2

    def test_constant_address_has_no_address_arithmetic(self) -> None:
        code, table, l2 = build(DECL_A + "x = A[3, 4];")
        self.assertEqual([(q.op, q.arg1) for q in l2.code.quads][0], (OP_LOAD, Indexed(1043, 0)))
        self.assertEqual(l2.entries[0].decision, "L2 applied (constant address, no run-time arithmetic)")
        self.assertEqual(run(l2.code, table, "L2").trace, [("R", 1043)])
        d = l2.code.accesses[0]
        self.assertEqual((d.first, d.last, d.access), (1, 0, 1))  # empty window

    def test_one_dimensional_unit_stride_needs_no_address_quadruple(self) -> None:
        _, _, l2 = build("array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end")
        self.assertEqual([q for q in l2.code.quads if q.tag == TAG_ADDR], [])
        self.assertEqual(l2.code.quads[1].arg1, Indexed(100, "i"))

    def test_zero_and_missing_coefficients_leave_no_term(self) -> None:
        # Di = 0 (A[3, j] does not depend on i) and a cancelled i in a subscript
        for subs in ("3, j", "i - i + 3, j"):
            _, _, l2 = build(a_access(subs))
            window = [q for q in l2.code.quads if q.tag == TAG_ADDR]
            self.assertEqual(window, [])  # only j with D = 1 remains: T is j itself
            self.assertEqual(l2.code.quads[2].arg1, Indexed(979 + 3 * 20, "j"))
            self.assertFalse(any("i" in (q.arg1, q.arg2) for q in l2.code.quads if q.tag == TAG_ADDR))

    def test_zero_coefficient_in_the_middle_loop(self) -> None:
        src = "array M[0:3, 0:4, 0:5] at 0;\nfor i = 0 to 3 do for j = 0 to 4 do for k = 0 to 5 do s = s + M[i, 2, k]; end end end"
        code, table, l2 = build(src)
        window = canon([q for q in l2.code.quads if q.tag == TAG_ADDR])
        self.assertEqual(window, [(TAG_ADDR, "*", "i", 30, "t1"), (TAG_ADDR, "+", "t1", "k", "t2")])  # j has D = 0
        self.assertEqual(l2.entries[0].plan.ds, (("i", 30), ("j", 0), ("k", 1)))
        self.assertEqual(l2.entries[0].plan.k, 12)

    def test_negative_coefficient(self) -> None:
        _, table, l2 = build(a_access("11 - i, j"))
        ops = [(q.op, q.arg1, q.arg2) for q in l2.code.quads if q.tag == TAG_ADDR]
        self.assertEqual(ops[0], ("*", "i", -20))
        self.assertEqual(next(q for q in l2.code.quads if q.op == OP_LOAD).arg1.base, 1199)
        trace = [a for _, a in run(l2.code, table, "L2").trace]
        self.assertEqual(trace[0], 1199 - 20 + 1)  # i = 1, j = 1: A[10, 1] = 1000 + 9*20
        self.assertEqual(trace[0], 1180)

    def test_negative_zero_and_constant_term_on_one_dimension(self) -> None:
        for subs, k, ds in [("5 - i", 99 + 5, (("i", -1),)), ("0 * i + 3", 99 + 3, (("i", 0),)), ("2 * i", 99, (("i", 2),))]:
            (p,) = plans(f"array V[1:20] at 100;\nfor i = 1 to 4 do s = s + V[{subs}]; end")
            self.assertEqual((p.k, p.ds), (k, ds))

    def test_one_to_four_dimensions(self) -> None:
        cases = [
            ("array V[2:9] at 10;\nfor i = 2 to 5 do s = s + V[i]; end", 1),
            ("array A[1:3, 0:4] at 5;\nfor i = 1 to 3 do for j = 0 to 4 do s = s + A[i, j]; end end", 2),
            ("array M[0:2, 1:3, 2:5] at 0;\nfor i = 0 to 2 do for j = 1 to 3 do for k = 2 to 5 do s = s + M[i, j, k]; end end end", 3),
            ("array H[1:2, 0:1, 1:3, 0:2] at 9;\nfor a = 1 to 2 do for b = 0 to 1 do for c = 1 to 3 do for d = 0 to 2 do s = s + H[a, b, c, d]; end end end end", 4),
        ]
        for src, n in cases:
            code, table, l2 = build(src)
            with self.subTest(dimensions=n):
                a, b = run(code, table, "L1"), run(l2.code, table, "L2")
                self.assertEqual((a.scalars, a.memory, a.trace), (b.scalars, b.memory, b.trace))
                self.assertEqual(len(l2.entries[0].plan.ds), n)
                self.assertEqual([x for _, x in b.trace], sorted(x for _, x in b.trace))  # row-major traversal ascends

    def test_reads_and_writes(self) -> None:
        src = "array A[1:4, 1:5] at 0;\nfor i = 1 to 4 do for j = 1 to 5 do A[i, j] = A[i, j] * 2 + i + j; end end"
        code, table, l2 = build(src)
        read, write = l2.entries
        self.assertEqual((read.report.is_write, write.report.is_write), (False, True))
        ops = [q.op for q in l2.code.quads if q.op in (OP_LOAD, OP_STORE)]
        self.assertEqual(ops, [OP_LOAD, OP_STORE])
        a, b = run(code, table, "L1"), run(l2.code, table, "L2")
        self.assertEqual((a.scalars, a.memory, a.trace), (b.scalars, b.memory, b.trace))
        self.assertEqual([k for k, _ in b.trace[:2]], ["R", "W"])

    def test_nested_and_sequential_loops_and_multiple_accesses(self) -> None:
        src = ("array A[1:6, 1:6] at 0;\narray V[1:6] at 100;\n"
               "for i = 1 to 3 do\n  V[i] = i;\n  for j = 2 to 4 do\n    s = s + A[j, i] + A[i + 1, 2 * j - 1] + V[j];\n  end\n  s = s + V[i];\nend\n"
               "for j = 1 to 5 do\n  s = s + A[j, 3] + V[j + 1];\nend\ns = s + A[2, 2] + V[6];")
        code, table, l2 = build(src)
        self.assertEqual(len(l2.entries), 9)
        self.assertTrue(all(e.applied for e in l2.entries))
        a, b = run(code, table, "L1"), run(l2.code, table, "L2")
        self.assertEqual((a.scalars, a.memory, a.trace), (b.scalars, b.memory, b.trace))
        self.assertEqual((a.counters.loads, a.counters.stores), (b.counters.loads, b.counters.stores))
        # the loop table and the descriptors describe the new listing
        for e in l2.code.loops:
            f, g = l2.code.quads[e.for_pos - 1], l2.code.quads[e.endfor_pos - 1]
            self.assertEqual((f.op, f.arg1, f.arg2, f.result), ("FOR", e.var, e.lo, e.hi))
            self.assertEqual((g.op, g.arg1), ("ENDFOR", e.var))
        self.assertEqual(len(analyze_accesses(l2.code)), 9)  # the L2 listing can be analysed again
        validate_windows(l2.code.quads, l2.code.accesses)

    def test_input_listing_is_not_modified(self) -> None:
        code, table, _ = build(a_access("2*i + 1, j - 1"))
        before = (list(code.quads), list(code.accesses), list(code.loops))
        optimize_l2(code, table)
        self.assertEqual((code.quads, code.accesses, code.loops), before)

    def test_l2_is_well_formed_and_levelled(self) -> None:
        for src in BruteForceTests.PROGRAMS:
            code, table, l2 = build(src)
            self.assertEqual(well_formedness_errors(l2.code.quads), [], src[:40])
            self.assertEqual(l2.code.level, "L2")
            self.assertIsInstance(l2, L2Program)

    def test_l0_input_gives_the_same_l2_behaviour(self) -> None:
        for src in BruteForceTests.PROGRAMS[:6]:
            code0, table, l2 = build(src, "L0")
            code1, _, l2b = build(src, "L1")
            self.assertEqual(canon(l2.code.quads), canon(l2b.code.quads))


# -- fallback -------------------------------------------------------------------------------------


FALLBACK_PROGRAMS = [
    "array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i * j]; end end",
    "array V[0:9] at 0;\narray W[0:9] at 100;\nfor i = 0 to 9 do W[i] = 9 - i; end\nfor i = 0 to 9 do s = s + V[W[i]]; end",
    "array A[1:10, 1:10] at 0;\nk = 3;\nfor i = 1 to 4 do s = s + A[i + k, 1]; end",
    "array A[1:10, 1:10] at 0;\nfor i = 1 to 3 do for j = 1 to 3 do A[i, j * j] = i + j; end end",
    # a fallback with an optimizable access around it, and a nested 2-D read inside a fallback window
    ("array A[1:10, 1:10] at 0;\narray B[1:10, 1:10] at 200;\narray V[0:1008] at 400;\n"
     "for i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j] + B[i, j * j] + V[A[i, j]] + A[2 * i, j + 1]; end end"),
]


class FallbackTests(unittest.TestCase):
    def test_fallback_window_is_byte_for_byte_the_l1_window(self) -> None:
        for src in FALLBACK_PROGRAMS:
            code, table, l2 = build(src)
            with self.subTest(src=src[:45]):
                fallbacks = [e for e in l2.entries if not e.report.optimizable]
                self.assertTrue(fallbacks)
                for e in fallbacks:
                    self.assertFalse(e.applied)
                    self.assertIsNone(e.plan)
                    self.assertTrue(e.decision.startswith("kept at L1 (subscript"))
                    self.assertEqual(e.old_code, e.new_code)  # same Quad records, names and tags included
                    self.assertEqual(tuple(l2.code.quads[e.new_first - 1:e.new_access]), e.old_code)

    def test_no_partial_optimization_of_a_mixed_access(self) -> None:
        code, table, l2 = build(FALLBACK_PROGRAMS[3])
        (e,) = l2.entries
        self.assertFalse(e.applied)
        self.assertIsInstance(e.report.forms[0], LinearForm)  # subscript 1 is linear, still untouched
        self.assertEqual(l2.code.quads, code.quads)  # the whole listing is identical

    def test_access_nested_in_a_fallback_window_stays_at_l1(self) -> None:
        src = ("array A[1:10, 1:10] at 0;\narray V[0:1008] at 400;\n"
               "for i = 1 to 3 do for j = 1 to 3 do s = s + V[A[i, j] + 0 * 5 + A[i, j] * A[i, j]]; end end")
        code, table, l2 = build(src)
        decisions = [e.decision for e in l2.entries]
        self.assertTrue(any("inside the window of a fallback access" in d for d in decisions))
        self.assertEqual(l2.code.quads, code.quads)

    def test_optimizable_neighbours_are_still_rewritten(self) -> None:
        code, table, l2 = build(FALLBACK_PROGRAMS[4])
        applied = [e.report.source for e in l2.entries if e.applied]
        self.assertEqual(applied, ["A[i, j]", "A[2 * i, j + 1]"])
        self.assertLess(len(l2.code.quads), len(code.quads) + 1)

    def test_fallback_programs_run_identically_at_l1_and_l2(self) -> None:
        for src in FALLBACK_PROGRAMS:
            code, table, l2 = build(src)
            with self.subTest(src=src[:45]):
                a, b = run(code, table, "L1"), run(l2.code, table, "L2")
                self.assertEqual((a.scalars, a.memory, a.trace), (b.scalars, b.memory, b.trace))
                self.assertTrue(b.trace)

    def test_fallback_reason_is_kept_in_the_report(self) -> None:
        _, _, l2 = build(FALLBACK_PROGRAMS[0])
        text = format_l2_report(l2)
        self.assertIn("decision     : kept at L1 (subscript 1: NOT LINEAR: product of loop variables (i * j))", text)
        self.assertNotIn("K = ", text)
        self.assertNotIn("replaced", text)


# -- multiplications and counters ---------------------------------------------------------------------


class MultiplicationTests(unittest.TestCase):
    def test_no_multiplication_by_dimension_sizes_remains(self) -> None:
        """Every ADDR multiplication of L2 is ``loop variable * D_s`` with a literal D_s != 1; none has a temporary operand."""
        for src in BruteForceTests.PROGRAMS:
            code, table, l2 = build(src)
            for e in l2.entries:
                window = l2.code.quads[e.new_first - 1:e.new_last]
                mults = [q for q in window if q.op == "*"]
                wanted = [(v, d) for v, d in e.plan.terms if d != 1]
                self.assertEqual([(q.arg1, q.arg2) for q in mults], wanted, src[:40])
                for q in window:  # no Horner chain: no multiplication consumes an earlier result
                    if q.op == "*":
                        self.assertFalse(isinstance(q.arg1, str) and q.arg1.startswith("T"))
                self.assertEqual(sum(1 for q in window if q.op == "+"), max(0, len(e.plan.terms) - 1))
                self.assertEqual(sum(1 for q in window if q.op == "-"), 0)

    def test_l2_never_has_more_address_work_than_l1(self) -> None:
        for src in BruteForceTests.PROGRAMS:
            code, table, l2 = build(src)
            a, b = run(code, table, "L1").counters, run(l2.code, table, "L2").counters
            with self.subTest(src=src[:40]):
                self.assertLessEqual(b.addr, a.addr)
                self.assertLessEqual(b.addr_multiplications, a.addr_multiplications)
                self.assertEqual((a.loads, a.stores), (b.loads, b.stores))
                self.assertEqual(sum(b.by_op.values()), b.total)

    def test_t04_counts_match_the_hand_prediction(self) -> None:
        """quad_spec.md 7.2: L1 152 mult / 228 add-sub; L2 76 mult / 76 add (address arithmetic only)."""
        src = DECL_A + "for i = 1 to 4 do for j = 2 to 20 do s = s + A[2*i + 1, j - 1]; end end"
        code, table, l2 = build(src)
        a, b = run(code, table, "L1").counters, run(l2.code, table, "L2").counters
        self.assertEqual((a.addr_multiplications, a.addr_add_sub), (152, 228))
        self.assertEqual((b.addr_multiplications, b.addr_add_sub), (76, 76))
        self.assertEqual(a.total - b.total, 3 * 76)  # 5 address quadruples became 2, 76 times

    def test_l2_equals_l1_for_the_simple_2d_access(self) -> None:
        code, table, l2 = build(a_access("i, j"))
        a, b = run(code, table, "L1").counters, run(l2.code, table, "L2").counters
        self.assertEqual((a.addr, a.addr_multiplications), (b.addr, b.addr_multiplications))

    def test_a_multiplication_free_access_when_every_d_is_zero_or_one(self) -> None:
        code, table, l2 = build("array A[1:4, 1:1] at 0;\nfor i = 1 to 4 do s = s + A[i, 1]; end")
        self.assertEqual(l2.entries[0].plan.ds, (("i", 1),))
        self.assertEqual(run(l2.code, table, "L2").counters.addr, 0)


# -- all examples ------------------------------------------------------------------------------------


class ExampleTests(unittest.TestCase):
    def test_l2_matches_l1_on_every_example(self) -> None:
        self.assertEqual(len(EXAMPLES), 5)
        for path in EXAMPLES:
            code, table, l2 = build(path.read_text(encoding="utf-8"))
            with self.subTest(example=path.name):
                a, b = run(code, table, "L1"), run(l2.code, table, "L2")
                self.assertEqual(a.scalars, b.scalars)
                self.assertEqual(a.memory, b.memory)
                self.assertEqual(a.checksum, b.checksum)
                self.assertEqual(a.trace, b.trace)
                self.assertTrue(b.trace)
                self.assertTrue(all(e.applied for e in l2.entries))
                self.assertEqual(len(l2.entries), len(code.accesses))
                self.assertLessEqual(b.counters.addr, a.counters.addr)

    def test_2d_traversal_trace_is_1000_to_1199(self) -> None:
        code, table, l2 = build((ROOT / "examples" / "2d_traversal.ml").read_text(encoding="utf-8"))
        self.assertEqual([a for _, a in run(l2.code, table, "L2").trace], list(range(1000, 1200)))

    def test_matrix_multiply_hoistable_accesses_have_zero_innermost_coefficients(self) -> None:
        code, table, l2 = build((ROOT / "examples" / "matrix_multiply.ml").read_text(encoding="utf-8"))
        z = [e for e in l2.entries if e.report.array == "Z" and len(e.report.loops) == 3]
        for e in z:
            self.assertEqual(dict(e.plan.ds)["k"], 0)


# -- report text -----------------------------------------------------------------------------------


class ReportTests(unittest.TestCase):
    def test_t04_report(self) -> None:
        src = DECL_A.rstrip("\n") + "\nfor i = 1 to 4 do\n  for j = 2 to 20 do\n    s = s + A[2*i + 1, j - 1];\n  end\nend"
        _, _, l2 = build(src)
        lines = format_l2_report(l2).splitlines()
        self.assertEqual(lines[0], "== Optimization report " + "=" * 51)
        self.assertEqual(lines[1], " access A[2 * i + 1, j - 1]  (read, line 4)")
        self.assertEqual(lines[2], "   linear forms : subscript 1 = 1 + 2*i ; subscript 2 = -1 + 1*j")
        self.assertEqual(lines[3], "   K = 998   D_i = 40   D_j = 1   (CONSPART 979, D0 19)")
        self.assertEqual(lines[4], "   address      : 998 + 40*i + j")
        self.assertEqual(lines[5], "   window       : L1 quadruples 3..8 -> L2 quadruples 3..5")
        self.assertEqual(sum(1 for l in lines if re.match(r"^   .{14} : +[0-9]+  (ADDR|BODY)", l)), 6 + 3)
        self.assertTrue(lines[-1].endswith("L2 applied"))
        self.assertIn("ADDR *     i         40", "\n".join(lines))
        self.assertIn("998[", "\n".join(lines))

    def test_constant_address_report(self) -> None:
        _, _, l2 = build(DECL_A + "x = A[3, 4];")
        text = format_l2_report(l2)
        self.assertIn("K = 1043   (CONSPART 979, D0 64)", text)
        self.assertIn("address      : 1043", text)
        self.assertIn("decision     : L2 applied (constant address, no run-time arithmetic)", text)

    def test_report_without_arrays(self) -> None:
        _, _, l2 = build("for i = 1 to 3 do s = s + i; end")
        self.assertEqual(format_l2_report(l2).splitlines(), ["== Optimization report " + "=" * 51])
        self.assertEqual(l2.entries, [])


class IndependenceTests(unittest.TestCase):
    def test_precompute_is_pure_arithmetic_on_phase_2_and_7_records(self) -> None:
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "import arraypeep.precompute;"
                "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True).stdout
        for forbidden in ("quad", "codegen", "interpreter", "optimizer", "parser", "lexer", "semantic"):
            self.assertNotIn(f"arraypeep.{forbidden}'", out)

    def test_optimizer_still_does_not_load_the_interpreter(self) -> None:
        code = ("import sys; sys.path.insert(0, sys.argv[1]);"
                "import arraypeep.optimizer;"
                "print(sorted(m for m in sys.modules if m.startswith('arraypeep.')))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True, text=True, check=True).stdout
        for forbidden in ("interpreter", "metrics", "loop_opt", "driver", "report"):
            self.assertNotIn(f"arraypeep.{forbidden}'", out)


if __name__ == "__main__":
    unittest.main()
