"""Tests for arraypeep.report (Phase 10.4: centralized report formatting).

Layouts are those of docs/quad_spec.md Section 9. Expected texts are written
out by hand from the samples there (T04 values are hand-derived in the
roadmap), so they check the layout itself rather than re-running the formatter.
"""
import unittest

from arraypeep import __main__ as cli
from arraypeep import report
from arraypeep.driver import (
    LevelComparison,
    LevelOutcome,
    Verification,
    compare_runs,
    run_source,
    verify,
)
from arraypeep.interpreter import RunResult, RuntimeAccessError, StepLimitError
from arraypeep.loop_opt import HoistedProgram, format_l3_report, hoist_l2
from arraypeep.metrics import Counters
from arraypeep.optimizer import format_l2_report
from arraypeep.quad import OP_ENDFOR, OP_FOR, OP_LOAD, Indexed, Quad
from arraypeep.symbol_table import format_information_vectors

T04 = (
    "array A[1:10, 1:20] at 1000;\n"
    "for i = 1 to 4 do\n"
    "  for j = 2 to 20 do\n"
    "    s = s + A[2*i + 1, j - 1];\n"
    "  end\n"
    "end\n"
)


def bar(title: str) -> str:
    """A block header written out literally: ``== title `` padded with ``=`` to 74 characters."""
    text = f"== {title} "
    return text + "=" * (74 - len(text))


def make_run(scalars=None, memory=None, trace=None) -> RunResult:
    return RunResult(
        {"s": 5} if scalars is None else scalars,
        {10: 1, 11: 2, 12: 3} if memory is None else memory,
        [("R", 10), ("R", 11), ("W", 12)] if trace is None else trace,
        Counters(),
    )


def counters(static: int, records: list[tuple[str, str, int]]) -> Counters:
    c = Counters(static=static)
    for op, tag, times in records:
        for _ in range(times):
            c.record(op, tag)
    return c


class HeaderTests(unittest.TestCase):
    def test_header_is_74_characters_wide(self):
        for title in ("Metrics", "Verification", "Quadruples (L1)", "Run hoist", "x" * 60):
            with self.subTest(title=title):
                text = report.format_header(title)
                self.assertEqual(len(text), 74)
                self.assertTrue(text.startswith(f"== {title} "))
                self.assertTrue(text.endswith("="))

    def test_header_matches_the_literal_layout(self):
        self.assertEqual(report.format_header("Metrics"), bar("Metrics"))

    def test_a_very_long_title_is_not_truncated(self):
        title = "t" * 90
        self.assertTrue(report.format_header(title).startswith(f"== {title} "))


class VectorTests(unittest.TestCase):
    def test_t04_vector_block_matches_the_spec(self):
        r = run_source(T04)
        expected = "\n".join([
            bar("Array information vector"),
            " array A   n = 2   type = int   a = 1000",
            "  dim  l   u   d",
            "   1   1   10  10     stride 20",
            "   2   1   20  20     stride 1",
            " C = 21    CONSPART = a - C = 979    size = 200",
        ])
        self.assertEqual(report.format_vectors(r.symtab.arrays), expected)

    def test_delegates_to_the_symbol_table_formatter(self):
        r = run_source(T04)
        self.assertEqual(report.format_vectors(r.symtab.arrays), format_information_vectors(r.symtab.arrays))

    def test_no_arrays(self):
        self.assertEqual(report.format_vectors(()), f"{bar('Array information vector')}\n (no arrays declared)")


class ListingTests(unittest.TestCase):
    QUADS = [
        Quad(OP_FOR, "i", 1, 10),
        Quad(OP_FOR, "j", 1, 20),
        Quad("*", "i", 20, "T1", "ADDR"),
        Quad("+", "T1", "j", "T2", "ADDR"),
        Quad(OP_LOAD, Indexed(979, "T2"), None, "T3"),
        Quad("+", "s", "T3", "T4"),
        Quad(":=", "T4", None, "s"),
        Quad(OP_ENDFOR, "j"),
        Quad(OP_ENDFOR, "i"),
    ]

    def test_spec_sample_layout_exactly(self):
        expected = "\n".join([
            bar("Quadruples (L1)"),
            "  1  BODY  FOR     i        1        10",
            "  2  BODY  FOR     j        1        20",
            "  3  ADDR  *       i        20       T1",
            "  4  ADDR  +       T1       j        T2",
            "  5  BODY  =[ ]    979[T2]  -        T3",
            "  6  BODY  +       s        T3       T4",
            "  7  BODY  :=      T4       -        s",
            "  8  BODY  ENDFOR  j",
            "  9  BODY  ENDFOR  i",
        ])
        self.assertEqual(report.format_quadruple_listing(self.QUADS, "L1"), expected)

    def test_title_shows_the_level_in_parentheses(self):
        for level in ("L0", "L1", "L2", "hoist", "L3"):
            with self.subTest(level=level):
                first = report.format_quadruple_listing(self.QUADS, level).split("\n")[0]
                self.assertEqual(first, bar(f"Quadruples ({level})"))

    def test_empty_listing_has_only_the_header(self):
        self.assertEqual(report.format_quadruple_listing([], "L0"), bar("Quadruples (L0)"))

    def test_line_numbers_are_right_aligned_to_width_three(self):
        quads = [Quad(":=", n, None, "s") for n in range(12)]
        lines = report.format_quadruple_listing(quads, "L0").split("\n")
        self.assertTrue(lines[1].startswith("  1  "))
        self.assertTrue(lines[10].startswith(" 10  "))
        self.assertTrue(lines[12].startswith(" 12  "))

    def test_real_l2_listing_of_t04(self):
        r = run_source(T04, "L1", "L2")
        lines = report.format_quadruple_listing(r.code.quads, "L2").split("\n")
        self.assertEqual(lines[0], bar("Quadruples (L2)"))
        self.assertEqual(lines[3], "  3  ADDR  *       i        40       T8")
        self.assertEqual(lines[5], "  5  BODY  =[ ]    998[T9]  -        T6")

    def test_no_trailing_newline(self):
        self.assertFalse(report.format_quadruple_listing(self.QUADS, "L1").endswith("\n"))


class OptimizationReportTests(unittest.TestCase):
    def test_l3_report_exact_text_for_t04(self):
        r = run_source(T04, "L1", "L3")
        expected = "\n".join([
            bar("Optimization report"),
            " access A[2 * i + 1, j - 1]  (read, line 4)",
            "   linear forms : subscript 1 = 1 + 2*i ; subscript 2 = -1 + 1*j",
            "   K = 998   D_i = 40   D_j = 1",
            "   arithmetic   : L2 2, L3 1 per innermost iteration",
            "   decision     : L3 applied (address arithmetic per innermost iteration drops from 2 to 1)",
        ])
        self.assertEqual(report.format_optimization_report(r.optimized.program), expected)

    def test_l2_report_lines_for_t04(self):
        r = run_source(T04, "L1", "L2")
        lines = report.format_optimization_report(r.l2).split("\n")
        self.assertEqual(lines[:4], [
            bar("Optimization report"),
            " access A[2 * i + 1, j - 1]  (read, line 4)",
            "   linear forms : subscript 1 = 1 + 2*i ; subscript 2 = -1 + 1*j",
            "   K = 998   D_i = 40   D_j = 1   (CONSPART 979, D0 19)",
        ])
        self.assertEqual(lines[4], "   address      : 998 + 40*i + j")
        self.assertEqual(lines[-1], "   decision     : L2 applied")

    def test_dispatch_by_program_type(self):
        for stage, text_of in (("L2", format_l2_report), ("L3", format_l3_report)):
            with self.subTest(stage=stage):
                r = run_source(T04, "L1", stage)
                program = r.l2 if stage == "L2" else r.optimized.program
                self.assertEqual(report.format_optimization_report(program), text_of(program))

    def test_the_hoist_stage_reports_from_its_l2_program(self):
        r = run_source(T04, "L1", "hoist")
        self.assertEqual(report.format_optimization_report(r.l2), format_l2_report(r.l2))

    def test_unsupported_program_types_are_rejected(self):
        r = run_source(T04, "L1", "L3")
        for bad in (hoist_l2(r.l2), r.optimized, None, "text"):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(TypeError):
                    report.format_optimization_report(bad)
        self.assertIsInstance(hoist_l2(r.l2), HoistedProgram)

    def test_mixed_decisions_are_all_shown(self):
        r = run_source(
            "array A[1:10, 1:20] at 1000; array V[0:9] at 100;\n"
            "for i = 1 to 9 do for j = 1 to 9 do s = s + A[i, j] + V[i]; end end\n",
            "L1", "L3",
        )
        text = report.format_optimization_report(r.optimized.program)
        self.assertEqual(text.count(" access "), 2)
        self.assertIn("decision     : L3 applied (address arithmetic per innermost iteration drops from 2 to 1)", text)
        self.assertIn("decision     : kept at L2 (no address arithmetic to save: 0 at L2, 0 at L3 per innermost iteration)", text)

    def test_non_linear_fallback_report(self):
        r = run_source("array V[0:99] at 0;\nfor i = 0 to 9 do for j = 0 to 9 do s = s + V[i * j]; end end\n", "L1", "L3")
        text = report.format_optimization_report(r.optimized.program)
        self.assertIn("NOT LINEAR: product of loop variables", text)
        self.assertIn("decision     : kept at L1 (", text)


class RunBlockTests(unittest.TestCase):
    def test_run_block_is_header_plus_result_text(self):
        run = run_source(T04, "L1", "L3").run
        self.assertEqual(report.format_run("L3", run), f"{bar('Run L3')}\n{run.format()}")

    def test_run_block_for_every_level_name(self):
        run = make_run()
        for level in ("L0", "L1", "L2", "hoist", "L3"):
            self.assertTrue(report.format_run(level, run).startswith(bar(f"Run {level}")))


class MetricsTests(unittest.TestCase):
    C0 = counters(14, [("*", "ADDR", 3), ("+", "ADDR", 2), ("-", "BODY", 1), ("+", "BODY", 4), (":=", "BODY", 5)])
    C3 = counters(11, [("+", "ADDR", 10), ("*", "BODY", 2)])

    def test_exact_table_derived_from_counters(self):
        # C0: static 14; total 3+2+1+4+5 = 15; ADDR 3+2 = 5; multiplications 3; add/sub 2+1+4 = 7
        # C3: static 11; total 12; ADDR 10; multiplications 2; add/sub 10
        expected = "\n".join([
            bar("Metrics"),
            " level | static quads | dynamic quads | dynamic ADDR | multiplications | add/sub",
            " L0    |           14 |            15 |            5 |               3 |       7",
            " L3    |           11 |            12 |           10 |               2 |      10",
        ])
        self.assertEqual(report.format_metrics([("L0", self.C0), ("L3", self.C3)]), expected)

    def test_the_header_matches_the_spec_text(self):
        header = report.format_metrics([("L0", self.C0)]).split("\n")[1]
        self.assertEqual(header, " level | static quads | dynamic quads | dynamic ADDR | multiplications | add/sub")
        self.assertEqual(report.METRICS_COLUMNS,
                         ("static quads", "dynamic quads", "dynamic ADDR", "multiplications", "add/sub"))

    def test_level_column_widens_for_longer_names(self):
        lines = report.format_metrics([("L0", self.C0), ("hoist", self.C3), ("a-long-level", self.C0)]).split("\n")
        self.assertEqual(lines[1], " level        | static quads | dynamic quads | dynamic ADDR | multiplications | add/sub")
        self.assertEqual(len({line.index("|") for line in lines[1:]}), 1)  # the bars line up

    def test_columns_line_up_under_their_titles(self):
        lines = report.format_metrics([("L0", self.C0), ("L3", self.C3)]).split("\n")
        bars = [i for i, ch in enumerate(lines[1]) if ch == "|"]
        for row in lines[2:]:
            self.assertEqual([i for i, ch in enumerate(row) if ch == "|"], bars)
            self.assertEqual(len(row), len(lines[1]))

    def test_numbers_come_from_the_counters_not_from_constants(self):
        other = counters(1, [("*", "BODY", 7)])
        table = report.format_metrics([("L0", other)])
        self.assertEqual(table.split("\n")[2].split("|")[1:], ["            1 ", "             7 ", "            0 ",
                                                              "               7 ", "       0"])

    def test_every_cell_equals_a_counter_property(self):
        for source in (T04, "array V[0:9] at 100;\nfor i = 0 to 9 do s = s + V[i]; end\n"):
            for level, stage in (("L0", None), ("L1", None), ("L1", "L2"), ("L1", "hoist"), ("L1", "L3")):
                with self.subTest(level=level, stage=stage, source=source[:12]):
                    c = run_source(source, level, stage).run.counters
                    cells = [int(x) for x in report.format_metrics([("x", c)]).split("\n")[2].split("|")[1:]]
                    self.assertEqual(cells, [c.static, c.total, c.addr, c.multiplications, c.add_sub])

    def test_t04_measured_rows_follow_the_roadmap_prediction(self):
        # roadmap 5.5 (hand prediction): L2 76 multiplications; L3 none; ADDR additions 84 at L3
        rows = [(name, run_source(T04, "L1", stage).run.counters) for name, stage in (("L2", "L2"), ("L3", "L3"))]
        lines = report.format_metrics(rows).split("\n")
        l2, l3 = ([int(x) for x in line.split("|")[1:]] for line in lines[2:])
        self.assertEqual(l2[3], 76)
        self.assertEqual(l3[3], 0)
        self.assertEqual(rows[1][1].addr_multiplications, 0)
        self.assertEqual(rows[1][1].addr_add_sub, 84)

    def test_empty_table_has_only_the_header_rows(self):
        self.assertEqual(
            report.format_metrics([]),
            f"{bar('Metrics')}\n level | static quads | dynamic quads | dynamic ADDR | multiplications | add/sub",
        )

    def test_unary_minus_counts_as_a_subtraction(self):
        c = counters(1, [("-", "BODY", 2), ("+", "BODY", 1)])
        self.assertEqual(report.format_metrics([("L0", c)]).split("\n")[2].split("|")[5].strip(), "3")

    def test_input_is_not_modified(self):
        c = counters(3, [("+", "ADDR", 1)])
        before = (c.static, c.total, dict(c.by_op))
        report.format_metrics([("L0", c)])
        self.assertEqual((c.static, c.total, c.by_op), before)

    def test_table_is_deterministic(self):
        rows = [("L0", self.C0), ("L3", self.C3)]
        self.assertEqual(report.format_metrics(rows), report.format_metrics(rows))


class VerificationFormatTests(unittest.TestCase):
    def outcomes(self, **special):
        return [LevelOutcome(n, type("P", (), {"run": special.get(n, make_run())})) for n in ("L0", "L1", "L2", "L3")]

    def test_passing_block_matches_the_spec_sample(self):
        text = report.format_verification(verify(self.outcomes()))
        expected = "\n".join([
            bar("Verification"),
            " L0 vs L1 : scalars equal, memory equal, address trace equal   PASS",
            " L0 vs L2 : scalars equal, memory equal, address trace equal   PASS",
            " L0 vs L3 : scalars equal, memory equal, address trace equal   PASS",
            " verdict  : PASS",
        ])
        self.assertEqual(text, expected)

    def test_hoist_row_widens_the_label_column(self):
        outs = [LevelOutcome(n, type("P", (), {"run": make_run()})) for n in ("L0", "L1", "hoist", "L3")]
        lines = report.format_verification(verify(outs)).split("\n")
        self.assertEqual(lines[1], " L0 vs L1    : scalars equal, memory equal, address trace equal   PASS")
        self.assertEqual(lines[2], " L0 vs hoist : scalars equal, memory equal, address trace equal   PASS")
        self.assertEqual(lines[4], " verdict     : PASS")

    def test_failing_row_replaces_the_text_with_the_first_difference(self):
        bad = make_run(trace=[("R", 10), ("R", 12), ("W", 12)])
        text = report.format_verification(verify(self.outcomes(L2=bad)))
        lines = text.split("\n")
        difference = "address trace differs at access 2: L0 (R, 11) vs L2 (R, 12)"
        self.assertEqual(len(difference), 59)  # longer than the 48-character PASS text, so the PASS row is padded
        self.assertEqual(lines[1], " L0 vs L1 : scalars equal, memory equal, address trace equal" + " " * 11 + "   PASS")
        self.assertEqual(lines[2], f" L0 vs L2 : {difference}   FAIL")
        self.assertEqual(lines[4], " verdict  : FAIL")

    def test_failing_rows_are_padded_so_the_verdicts_line_up(self):
        bad = make_run(scalars={"s": 6})
        lines = report.format_verification(verify(self.outcomes(L2=bad))).split("\n")
        self.assertEqual(lines[1].rindex("PASS"), lines[2].rindex("FAIL"))

    def test_a_long_difference_widens_every_row(self):
        bad = make_run(scalars={"a_long_scalar_name": 99999999, "s": 5})
        lines = report.format_verification(verify(self.outcomes(L2=bad))).split("\n")
        self.assertEqual(lines[1].rindex("PASS"), lines[2].rindex("FAIL"))
        self.assertIn("scalar a_long_scalar_name is missing in L0", lines[2])

    def test_multiple_failed_checks_show_only_the_first_but_keep_all_three_results(self):
        bad = make_run(scalars={"s": 6}, memory={10: 0, 11: 2, 12: 3}, trace=[("R", 10)])
        verification = verify(self.outcomes(L2=bad))
        comparison = verification.comparisons[1]
        self.assertEqual([c.passed for c in comparison.checks], [False, False, False])
        lines = report.format_verification(verification).split("\n")
        self.assertIn("scalar s differs: L0 5 vs L2 6", lines[2])
        self.assertNotIn("memory differs", "\n".join(lines))
        self.assertNotIn("address trace differs", "\n".join(lines))
        self.assertEqual([c.name for c in comparison.checks], ["scalars", "memory", "address trace"])

    def test_memory_only_and_trace_only_failures(self):
        mem = report.format_verification(verify(self.outcomes(L1=make_run(memory={10: 1, 11: 2, 12: 0})))).split("\n")
        self.assertIn("memory differs at address 12: L0 3 vs L1 0", mem[1])
        trace = report.format_verification(verify(self.outcomes(L3=make_run(trace=[])))).split("\n")
        self.assertIn("address trace differs at access 1: L0 (R, 10) vs L3 (end of trace)", trace[3])

    def test_every_failing_row_shows_its_own_difference(self):
        text = report.format_verification(verify(self.outcomes(
            L1=make_run(scalars={"s": 0}), L2=make_run(memory={}), L3=make_run(trace=[]))))
        lines = text.split("\n")
        self.assertEqual([line.rstrip().split()[-1] for line in lines[1:4]], ["FAIL", "FAIL", "FAIL"])
        self.assertEqual(lines[4], " verdict  : FAIL")
        self.assertIn("scalar s differs", lines[1])
        self.assertIn("memory address 10 is missing in L2", lines[2])
        self.assertIn("address trace differs at access 1", lines[3])

    def test_runtime_error_and_step_limit_rows(self):
        error = RuntimeAccessError("runtime error: bad address", 4, "L2")
        limit = StepLimitError("runtime error: step limit exceeded")
        v = verify([
            LevelOutcome("L0", type("P", (), {"run": make_run()})),
            LevelOutcome("L2", None, error),
            LevelOutcome("L3", None, limit),
        ])
        lines = report.format_verification(v).split("\n")
        self.assertRegex(lines[1], r"^ L0 vs L2 : runtime error at quadruple 4 where L0 completed +FAIL$")
        self.assertRegex(lines[2], r"^ L0 vs L3 : step limit exceeded where L0 completed +FAIL$")
        self.assertEqual(lines[3], " verdict  : FAIL")

    def test_baseline_only_verification(self):
        v = Verification("L0", ())
        self.assertEqual(report.format_verification(v), f"{bar('Verification')}\n verdict : PASS")

    def test_rows_follow_the_order_of_the_comparisons(self):
        a = compare_runs("L0", make_run(), "L3", make_run())
        b = compare_runs("L0", make_run(), "L1", make_run())
        lines = report.format_verification(Verification("L0", (a, b))).split("\n")
        self.assertTrue(lines[1].startswith(" L0 vs L3"))
        self.assertTrue(lines[2].startswith(" L0 vs L1"))

    def test_verdict_text_is_taken_from_the_verification(self):
        failing = Verification("L0", (LevelComparison("L0", "L1", (), RuntimeAccessError("x", 1, "L1")),))
        self.assertTrue(report.format_verification(failing).endswith(": FAIL"))

    def test_formatting_is_pure_and_deterministic(self):
        v = verify(self.outcomes(L2=make_run(scalars={"s": 6})))
        before = v
        first = report.format_verification(v)
        self.assertEqual(report.format_verification(v), first)
        self.assertEqual(v, before)

    def test_pass_text_constant(self):
        self.assertEqual(report.PASS_TEXT, "scalars equal, memory equal, address trace equal")


class CliUsesTheReportModuleTests(unittest.TestCase):
    def test_cli_has_no_private_formatters_any_more(self):
        for name in ("_header", "_format_verification", "PASS_TEXT"):
            self.assertFalse(hasattr(cli, name), name)

    def test_cli_output_equals_the_report_functions(self):
        import contextlib
        import io
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t04.ml"
            path.write_text(T04, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([str(path), "--level", "L3", "--show-vectors", "--show-quads",
                                           "--show-plan", "--run"]), 0)
            r = run_source(T04, "L1", "L3")
            expected = "\n\n".join([
                report.format_vectors(r.symtab.arrays),
                report.format_quadruple_listing(r.code.quads, "L3"),
                report.format_optimization_report(r.optimized.program),
                report.format_run("L3", r.run),
            ]) + "\n"
            self.assertEqual(out.getvalue(), expected)

    def test_listing_title_follows_the_phase_1_form(self):
        import contextlib
        import io
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t04.ml"
            path.write_text(T04, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main([str(path), "--level", "all", "--show-quads"])
        for level in ("L0", "L1", "L2", "hoist", "L3"):
            self.assertIn(bar(f"Quadruples ({level})"), out.getvalue())
        self.assertNotIn("== Quadruples L", out.getvalue())


if __name__ == "__main__":
    unittest.main()
