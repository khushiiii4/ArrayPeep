"""Robustness tests (Phase 11.5).

1. Non-linear subscripts must fall back to the baseline code and still pass the
   three equivalence checks (scalars, memory, address trace against L0 and the
   oracle) at every level. Covered: products of loop variables, indirect
   (array element) subscripts and scalars that are not loop variables.
2. Every kind of syntax and semantic error is reported through the real command
   line: exit code 1, exactly one line on standard error, nothing on standard
   output, with the position and message of docs/language_spec.md Sections 5 and 9.

The detailed rule-by-rule tests of the front end live in ``test_lexer.py``,
``test_parser.py`` and ``test_semantic.py`` (S1-S12); the second part here is
only an end-to-end sweep, so it does not repeat their variations.
"""
import contextlib
import io
import pathlib
import re
import sys
import tempfile
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:  # also works for `python -m unittest tests.test_robustness`
    sys.path.insert(0, str(TESTS_DIR))

import equivalence as eq  # noqa: E402
import oracle  # noqa: E402
import test_end_to_end as e2e  # noqa: E402  (helpers only; importing the module registers no tests here)
from oracle import Array, Loop, Statement, assign, linear, read, write  # noqa: E402

from arraypeep.__main__ import main  # noqa: E402

A = Array("A", (1, 1), (10, 20), 1000)  # A[1:10, 1:20] at 1000
V100 = Array("V", (0,), (99,), 0)  # V[0:99] at 0

PRODUCT = "product of loop variables"
SCALAR = "variable that is not a loop variable"
ARRAY = "array element in subscript"


class FallbackTests(unittest.TestCase):
    def check(self, name, source, arrays, body, kinds, reasons=(), sum_arrays=("A",)):
        """All levels equal L0 and the oracle; the L3 report has the expected decision kinds and fallback reasons."""
        trace = oracle.expected_trace(arrays, body)
        results = eq.assert_equivalent(self, source, trace, name)
        entries = results["L3"].optimized.entries
        self.assertEqual([e2e.decision_kind(e) for e in entries], list(kinds), name)
        fallback_reasons = [e.l2.report.reason for e in entries if e2e.decision_kind(e) == "fallback"]
        self.assertEqual(len(fallback_reasons), len(reasons), name)
        for got, want in zip(fallback_reasons, reasons):
            self.assertIn(want, got, name)
        for e in entries:
            if e2e.decision_kind(e) == "fallback":
                self.assertTrue(e.decision.startswith("kept at L1"), e.decision)
        if sum_arrays:
            spec = {"fixed": {}, "sum_of_reads": {"into": "s", "arrays": list(sum_arrays)}}
            want = e2e.expected_scalars(spec, trace, arrays)["s"]
            self.assertEqual({r.run.scalars["s"] for r in results.values()}, {want}, name)
        return trace, results

    def assert_all_levels_keep_the_l1_code(self, results):
        """When every access falls back no level may differ from L1: the listing is unchanged."""
        for level in ("L2", "hoist", "L3"):
            self.assertEqual(results[level].code.quads, results["L1"].code.quads, level)

    # products of loop variables

    def test_product_of_two_loop_variables(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 3 do for j = 1 to 3 do s = s + A[i * j, 1]; end end\n"
        body = e2e.loops(("i", 1, 3), ("j", 1, 3))(e2e.reads_stmt(read("A", lambda env: env["i"] * env["j"], 1)))
        trace, results = self.check("product i*j", source, [A], body, ["fallback"], [PRODUCT])
        self.assertEqual([a for _, a in trace], [1000 + 20 * (i * j - 1) for i in (1, 2, 3) for j in (1, 2, 3)])
        self.assert_all_levels_keep_the_l1_code(results)

    def test_loop_variable_times_itself(self):
        source = "array V[0:99] at 0;\ns = 0;\nfor i = 0 to 9 do s = s + V[i * i]; end\n"
        body = e2e.loops(("i", 0, 9))(e2e.reads_stmt(read("V", lambda env: env["i"] ** 2)))
        trace, results = self.check("product i*i", source, [V100], body, ["fallback"], [PRODUCT], ("V",))
        self.assertEqual(trace[-1], ("R", 81))
        self.assert_all_levels_keep_the_l1_code(results)

    def test_product_inside_a_linear_expression(self):
        source = "array V[0:99] at 0;\ns = 0;\nfor i = 0 to 4 do for j = 0 to 4 do s = s + V[2 * (i * j) + 1]; end end\n"
        body = e2e.loops(("i", 0, 4), ("j", 0, 4))(e2e.reads_stmt(read("V", lambda env: 2 * env["i"] * env["j"] + 1)))
        _, results = self.check("2*(i*j)+1", source, [V100], body, ["fallback"], [PRODUCT], ("V",))
        self.assert_all_levels_keep_the_l1_code(results)

    def test_one_non_linear_subscript_makes_the_whole_access_fall_back(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 3 do for j = 1 to 4 do s = s + A[i, j * j]; end end\n"
        body = e2e.loops(("i", 1, 3), ("j", 1, 4))(e2e.reads_stmt(read("A", "i", lambda env: env["j"] ** 2)))
        _, results = self.check("A[i, j*j]", source, [A], body, ["fallback"], [PRODUCT])
        self.assert_all_levels_keep_the_l1_code(results)
        self.assertIsNone(results["L3"].optimized.entries[0].l2.plan)

    # indirect subscripts

    def test_indirect_subscript_in_a_two_dimensional_access(self):
        w = Array("W", (1,), (4,), 0)
        a = Array("A", (1, 1), (10, 20), 1000)
        source = ("array W[1:4] at 0;\narray A[1:10, 1:20] at 1000;\n"
                  "for i = 1 to 4 do W[i] = i + 1; end\ns = 0;\n"
                  "for i = 1 to 4 do for j = 1 to 3 do s = s + A[W[i], j]; end end\n")
        body = [Loop("i", 1, 4, [assign(write("W", "i"))]),
                *e2e.loops(("i", 1, 4), ("j", 1, 3))([Statement([read("W", "i"), read("A", lambda env: env["i"] + 1, "j")])])]
        trace, results = self.check("A[W[i], j]", source, [w, a], body, ["kept-at-L2", "fallback", "inside-fallback-window"],
                                    [ARRAY], ("A",))
        # four writes of W first; then W[1] (address 0) is read and holds 2, so the next read is A[2, 1] = 1020
        self.assertEqual(trace[4:6], [("R", 0), ("R", 1020)])

    def test_indirect_subscript_on_the_assignment_target(self):
        w = Array("W", (0,), (9,), 0)
        v = Array("V", (0,), (9,), 10)
        source = ("array W[0:9] at 0;\narray V[0:9] at 10;\nfor i = 0 to 9 do W[i] = 9 - i; end\n"
                  "for i = 0 to 9 do V[W[i]] = i; end\n")
        body = [Loop("i", 0, 9, [assign(write("W", "i"))]),
                Loop("i", 0, 9, [Statement([read("W", "i"), write("V", lambda env: 9 - env["i"])])])]
        trace = oracle.expected_trace([w, v], body)
        results = eq.assert_equivalent(self, source, trace, "V[W[i]] = i")
        memory = oracle.initial_memory([w, v])
        for a in range(10):
            memory[a] = 9 - a  # W[i] = 9 - i
            memory[10 + (9 - a)] = a  # V[W[i]] = i  stores i at V[9 - i]
        for level, result in results.items():
            self.assertEqual(result.run.memory, memory, level)
        self.assertEqual(results["L3"].run.scalars, {})

    def test_double_indirection(self):
        u = Array("U", (0,), (4,), 0)
        w = Array("W", (0,), (4,), 10)
        v = Array("V", (0,), (4,), 20)
        source = ("array U[0:4] at 0;\narray W[0:4] at 10;\narray V[0:4] at 20;\n"
                  "for i = 0 to 4 do U[i] = 4 - i; end\nfor i = 0 to 4 do W[i] = U[i]; end\ns = 0;\n"
                  "for i = 0 to 4 do s = s + V[W[U[i]]]; end\n")
        # W[i] = U[i] = 4 - i; V[W[U[i]]] reads U[i] = 4-i, then W[4-i] = i, then V[i]
        body = [Loop("i", 0, 4, [assign(write("U", "i"))]),
                Loop("i", 0, 4, [assign(write("W", "i"), read("U", "i"))]),
                Loop("i", 0, 4, [Statement([read("U", "i"), read("W", lambda env: 4 - env["i"]), read("V", "i")])])]
        trace = oracle.expected_trace([u, w, v], body)
        results = eq.assert_equivalent(self, source, trace, "V[W[U[i]]]")
        want = sum(oracle.init_value(20 + i) for i in range(5))
        self.assertEqual({r.run.scalars["s"] for r in results.values()}, {want})
        outer = [e2e.decision_kind(e) for e in results["L3"].optimized.entries]
        self.assertIn("fallback", outer)

    # scalars that are not loop variables

    def test_scalar_assigned_before_the_loop(self):
        source = "array A[1:10, 1:20] at 1000;\nk = 3;\ns = 0;\nfor i = 1 to 5 do s = s + A[i + k, 2]; end\n"
        body = e2e.loops(("i", 1, 5))(e2e.reads_stmt(read("A", lambda env: env["i"] + 3, 2)))
        _, results = self.check("A[i + k, 2]", source, [A], body, ["fallback"], [SCALAR])
        self.assert_all_levels_keep_the_l1_code(results)
        self.assertEqual({r.run.scalars["k"] for r in results.values()}, {3})

    def test_scalar_changed_inside_the_loop(self):
        source = "array A[1:10, 1:20] at 1000;\nk = 0;\ns = 0;\nfor i = 1 to 5 do k = k + 1; s = s + A[k, 1]; end\n"
        body = e2e.loops(("i", 1, 5))(e2e.reads_stmt(read("A", "i", 1)))  # k == i at the access
        _, results = self.check("k = k + 1", source, [A], body, ["fallback"], [SCALAR])
        self.assertEqual({r.run.scalars["k"] for r in results.values()}, {5})

    def test_scalar_computed_from_the_loop_variable(self):
        source = "array A[1:10, 1:20] at 1000;\ns = 0;\nfor i = 1 to 5 do k = 2 * i; s = s + A[k, 1]; end\n"
        body = e2e.loops(("i", 1, 5))(e2e.reads_stmt(read("A", lambda env: 2 * env["i"], 1)))
        _, results = self.check("k = 2*i", source, [A], body, ["fallback"], [SCALAR])
        self.assertEqual({r.run.scalars["k"] for r in results.values()}, {10})

    # linear and non-linear accesses together

    def test_linear_access_is_still_optimized_next_to_a_fallback_access(self):
        source = ("array A[1:10, 1:20] at 1000;\ns = 0;\n"
                  "for i = 1 to 3 do for j = 1 to 3 do s = s + A[i, j] + A[i * j, 1]; end end\n")
        body = e2e.loops(("i", 1, 3), ("j", 1, 3))(
            e2e.reads_stmt(read("A", "i", "j"), read("A", lambda env: env["i"] * env["j"], 1)))
        _, results = self.check("linear + product", source, [A], body, ["L3", "fallback"], [PRODUCT])
        self.assertNotEqual(results["L3"].code.quads, results["L1"].code.quads)  # the linear access changed

    def test_every_non_linear_reason_in_one_program(self):
        source = ("array A[1:10, 1:20] at 1000;\narray W[1:3] at 0;\nfor i = 1 to 3 do W[i] = i; end\nk = 1;\ns = 0;\n"
                  "for i = 1 to 3 do for j = 1 to 3 do s = s + A[i * j, 1] + A[W[i], 1] + A[k, j]; end end\n")
        w = Array("W", (1,), (3,), 0)
        body = [Loop("i", 1, 3, [assign(write("W", "i"))]),
                *e2e.loops(("i", 1, 3), ("j", 1, 3))([Statement([
                    read("A", lambda env: env["i"] * env["j"], 1),
                    read("W", "i"), read("A", lambda env: env["i"], 1),  # W[i] holds i
                    read("A", 1, "j")])])]
        _, results = self.check("all reasons", source, [A, w], body,
                                ["kept-at-L2", "fallback", "fallback", "inside-fallback-window", "fallback"],
                                [PRODUCT, ARRAY, SCALAR])
        self.assertEqual(len(results), 5)


class ErrorReportingTests(unittest.TestCase):
    """Each error of language_spec.md Sections 5 and 9 reaches the user as one line, exit code 1, no stdout."""

    def run_cli(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "prog.ml"
            path.write_text(source, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([str(path)])
        return code, out.getvalue(), err.getvalue().replace(str(path), "prog.ml")

    def assert_reported(self, source, message):
        code, out, err = self.run_cli(source)
        self.assertEqual((code, out), (1, ""), source)
        self.assertEqual(err, message + "\n", source)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertNotIn("Traceback", err)

    SYNTAX = [
        # (program, expected message); line and column hand-counted from the spec's examples (Section 9)
        ("s = 0;\ns = 1;\ns = 2;\n  s = s + 1\nend\n", "prog.ml: line 4, col 12: syntax error: expected ';'"),
        ("array A[1:10, 1:20] at 1000;\nA[i, j = 1;\n", "prog.ml: line 2, col 7: syntax error: expected ']'"),
        ("array B[1:n] at 0;\n",
         "prog.ml: line 1, col 11: syntax error: array bounds must be integer constants "
         "(variable-bound arrays are not supported)"),
        ("for i = 1 to 3\n  s = 1;\nend\n", "prog.ml: line 1, col 15: syntax error: expected 'do'"),
        ("s = 3 $ 4;\n", "prog.ml: line 1, col 7: syntax error: illegal character '$'"),
        ("for i = 1 to 3 do\n", "prog.ml: line 1, col 18: syntax error: expected 'end' before end of file"),
    ]

    def test_syntax_errors(self):
        for source, message in self.SYNTAX:
            with self.subTest(source=source):
                self.assert_reported(source, message)

    SEMANTIC = [
        ("S1", "s = 1;\nQ[1] = 2;\n", "prog.ml: line 2: semantic error: array 'Q' is not declared"),
        ("S2", "array A[1:10, 1:20] at 1000;\ns = A[1];\n",
         "prog.ml: line 2: semantic error: array 'A' has 2 dimensions but 1 subscript(s) given"),
        ("S3", "array A[1:3] at 0;\ns = A + 1;\n", "prog.ml: line 2: semantic error: array 'A' used without subscripts"),
        ("S4", "array A[1:3] at 0;\narray A[1:3] at 10;\n", "prog.ml: line 2: semantic error: array 'A' is already declared"),
        ("S5", "array B[5:4] at 0;\n",
         "prog.ml: line 1: semantic error: upper bound 4 is less than lower bound 5 in dimension 1 of array 'B'"),
        ("S6", "array B[0:1,0:1,0:1,0:1,0:1] at 0;\n",
         "prog.ml: line 1: semantic error: array 'B' has 5 dimensions; at most 4 are supported"),
        ("S7", "array A[1:10, 1:20] at 1000;\narray B[1:20] at 1100;\n",
         "prog.ml: line 2: semantic error: array 'B' (addresses 1100..1119) overlaps array 'A' (addresses 1000..1199)"),
        ("S8", "for i = 1 to 3 do\n  i = 5;\nend\n", "prog.ml: line 2: semantic error: loop variable 'i' cannot be assigned"),
        ("S9", "for i = 1 to 3 do\n  for i = 1 to 2 do s = 1; end\nend\n",
         "prog.ml: line 2: semantic error: loop variable 'i' is already used by an enclosing loop"),
        ("S10", "array i[1:3] at 0;\nfor i = 1 to 2 do s = 1; end\n",
         "prog.ml: line 1: semantic error: 'i' is used as a loop variable and as an ordinary name"),
        ("S10", "for i = 1 to 2 do s = 1; end\ns = i;\n",
         "prog.ml: line 2: semantic error: 'i' is used as a loop variable and as an ordinary name"),
        ("S12", "T1 = 0;\n", "prog.ml: line 1: semantic error: 'T1' is reserved for compiler temporaries"),
    ]

    def test_semantic_errors(self):
        for rule, source, message in self.SEMANTIC:
            with self.subTest(rule=rule):
                self.assert_reported(source, message)

    def test_s11_reading_a_loop_variable_outside_its_loop_is_reported(self):
        # S11 is subsumed by S10 (language_spec.md Section 5); the user still gets exactly one semantic error
        code, out, err = self.run_cli("for i = 1 to 2 do s = i; end\ns = i;\n")
        self.assertEqual((code, out), (1, ""))
        self.assertRegex(err, r"^prog\.ml: line 2: semantic error: .*'i'.*\n$")

    def test_only_the_first_semantic_error_is_reported(self):
        code, out, err = self.run_cli("s = 1;\nQ[1] = 2;\nR[1] = 3;\n")
        self.assertEqual(err, "prog.ml: line 2: semantic error: array 'Q' is not declared\n")

    def test_only_the_first_syntax_error_is_reported(self):
        code, out, err = self.run_cli("s = 1 $ 2;\nt = 3 @ 4;\n")
        self.assertEqual(err, "prog.ml: line 1, col 7: syntax error: illegal character '$'\n")

    def test_a_syntax_error_wins_over_a_later_semantic_error(self):
        code, out, err = self.run_cli("Q[1] = 2;\ns = 3 $ 4;\n")
        self.assertIn("syntax error", err)
        self.assertNotIn("semantic", err)

    def test_every_semantic_rule_has_detailed_tests_in_test_semantic(self):
        text = (TESTS_DIR / "test_semantic.py").read_text(encoding="utf-8")
        names = re.findall(r"^class (S\d+(?:S\d+)*)", text, re.M)
        covered = {int(n) for name in names for n in re.findall(r"S(\d+)", name)}
        self.assertEqual(covered, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12})  # S11 is tested inside the S10 class
        self.assertIn("test_s11_is_subsumed_by_s10", text)
        self.assertIn("class SyntaxErrorTests", (TESTS_DIR / "test_parser.py").read_text(encoding="utf-8"))
        self.assertIn("class IllegalCharacterTests", (TESTS_DIR / "test_lexer.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
