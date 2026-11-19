"""All user-visible output formats: vectors, listings, plans, metrics tables, verdicts (Phase 10.4).

One module for every text block the command line prints, in the layouts of
docs/quad_spec.md Section 9 (block headers ``== <title> ===`` 74 characters
wide). The functions only format: they read result objects and never
recompute, compare or optimize anything. Where a block's text is already
built by the module that owns its data (the information vector by
``symbol_table``, the numbered listing by ``quad``, the per-access report by
``optimizer`` and ``loop_opt``, the run counters by ``interpreter``), these
functions are the single entry point and delegate to it. No function adds a
trailing newline.
"""
from collections.abc import Iterable, Sequence

from .driver import Verification
from .interpreter import RunResult
from .loop_opt import GuardedProgram, format_l3_report
from .metrics import Counters
from .optimizer import L2Program, format_l2_report
from .quad import LISTING_WIDTH, Quad, format_listing
from .symbol_table import ArrayInfo, format_information_vectors

PASS_TEXT = "scalars equal, memory equal, address trace equal"
"""The descriptive text of a passing verification row."""

METRICS_COLUMNS = ("static quads", "dynamic quads", "dynamic ADDR", "multiplications", "add/sub")
"""The measured columns of the metrics table (quad_spec.md 9.5), in order."""


def format_header(title: str) -> str:
    """A block header ``== <title> ===...`` of ``LISTING_WIDTH`` characters."""
    return f"== {title} ".ljust(LISTING_WIDTH, "=")


def format_vectors(arrays: Iterable[ArrayInfo]) -> str:
    """The array information vectors, one block per array in declaration order (quad_spec.md 9.1)."""
    return format_information_vectors(arrays)


def format_quadruple_listing(quads: Sequence[Quad], level: str) -> str:
    """The numbered quadruple listing under the header ``== Quadruples (<level>) ===`` (quad_spec.md 9.2)."""
    return format_listing(quads, title=f"Quadruples ({level})")


def format_optimization_report(program: "L2Program | GuardedProgram") -> str:
    """The per-access optimization report (quad_spec.md 9.3) of an L2 or a guarded L3 program.

    An ``L2Program`` (also the program the ``hoist`` stage starts from) gets
    the Phase 8 report; a ``GuardedProgram`` the Phase 9 report with the
    per-iteration arithmetic costs and the guard's decision. Any other type
    raises ``TypeError``.
    """
    if isinstance(program, GuardedProgram):
        return format_l3_report(program)
    if isinstance(program, L2Program):
        return format_l2_report(program)
    raise TypeError(f"no optimization report for {type(program).__name__}")


def format_run(level: str, run: RunResult) -> str:
    """The run result of ``level`` under the header ``== Run <level> ===`` (scalars, checksum, counters)."""
    return f"{format_header(f'Run {level}')}\n{run.format()}"


def format_metrics(rows: Sequence[tuple[str, Counters]]) -> str:
    """The metrics comparison table (quad_spec.md 9.5): one row per ``(level, counters)`` pair, in the order given.

    Every number is read from the counters of the run: static quadruples,
    dynamic quadruples, dynamic ``ADDR`` quadruples, multiplications and
    additions plus subtractions executed. Numbers are right-aligned under
    their column titles.
    """
    level_width = max([len("level")] + [len(level) for level, _ in rows])
    widths = [len(column) for column in METRICS_COLUMNS]
    lines = [format_header("Metrics")]
    lines.append(f" {'level':<{level_width}} | " + " | ".join(METRICS_COLUMNS))
    for level, c in rows:
        values = (c.static, c.total, c.addr, c.multiplications, c.add_sub)
        lines.append(f" {level:<{level_width}} | " + " | ".join(f"{v:>{w}}" for v, w in zip(values, widths)))
    return "\n".join(lines)


def format_verification(verification: Verification) -> str:
    """The verification block (quad_spec.md 9.4, without the oracle row of Phase 11).

    One row per compared level, ``<baseline> vs <level> : <text>   PASS|FAIL``.
    A passing row shows ``PASS_TEXT``; a failing row replaces it with the
    first difference of the comparison (the run-time error, else the first
    failing check; the other check results stay in the ``Verification``). The
    last line is the overall ``verdict``.
    """
    comparisons = verification.comparisons
    width = max((len(c.level) for c in comparisons), default=0)
    rows = [
        (f" {c.baseline} vs {c.level:<{width}}", PASS_TEXT if c.passed else c.first_difference,
         "PASS" if c.passed else "FAIL")
        for c in comparisons
    ]
    text_width = max([len(PASS_TEXT)] + [len(text) for _, text, _ in rows])
    lines = [format_header("Verification")]
    lines += [f"{label} : {text:<{text_width}}   {verdict}" for label, text, verdict in rows]
    label_width = len(rows[0][0]) if rows else 8
    lines.append(f"{' verdict':<{label_width}} : {verification.verdict}")
    return "\n".join(lines)
