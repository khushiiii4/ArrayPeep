"""Randomized differential test (Phase 11.6).

300 deterministic programs (5 fixed seeds x 60 programs) are generated from
plain ``random.Random`` streams. Each program declares 1-3 arrays of 1-4
dimensions with random bounds and base addresses (no overlap), runs 1-3 nested
loops (some zero-trip or single-iteration) and uses random linear subscripts
that are guaranteed to stay inside the array bounds. Every program is run at
L0, L1, L2, hoist and L3 through the 11.2 harness: scalars, the complete memory
image and the ordered address trace of every level are compared with L0, and the
trace of every level with the independent oracle (``tests/oracle.py``).

Program ``(seed, index)`` is generated from ``Random(seed * 10000 + index)``
alone, so any failure reproduces with ``generate_program(seed, index)``; every
failure message carries the seed, the index and the source.
"""
import pathlib
import random
import sys
import time
import unittest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:  # also works for `python -m unittest tests.test_random_differential`
    sys.path.insert(0, str(TESTS_DIR))

import equivalence as eq  # noqa: E402
import oracle  # noqa: E402
from oracle import Array, Loop, Statement, linear, read, write  # noqa: E402

SEEDS = (101, 202, 303, 404, 505)
PROGRAMS_PER_SEED = 60
LOOP_VARS = ("i", "j", "k")
ARRAY_NAMES = ("A", "B", "C")
MAX_CELLS = 300  # per array
MAX_ITERATIONS = 100  # product of the trip counts of one nest


class Program:
    """A generated program: its source text and the oracle description of the same program."""

    def __init__(self, seed, index, source, arrays, body, shape):
        self.seed = seed
        self.index = index
        self.source = source
        self.arrays = arrays
        self.body = body
        self.shape = shape  # facts about the program, used to check that the generator covers the interesting cases


# -- generator ---------------------------------------------------------------------------------

def _gen_arrays(rng):
    arrays, end = [], rng.randint(-40, 40)
    for name in ARRAY_NAMES[:rng.randint(1, 3)]:
        n = rng.choice((1, 1, 2, 2, 3, 4))
        sizes = [rng.randint(1, 6) for _ in range(n)]
        while _product(sizes) > MAX_CELLS:
            sizes[sizes.index(max(sizes))] -= 1
        lowers = tuple(rng.randint(-4, 4) for _ in range(n))
        uppers = tuple(l + s - 1 for l, s in zip(lowers, sizes))
        base = end + rng.randint(1, 15)
        arrays.append(Array(name, lowers, uppers, base))
        end = base + _product(sizes) - 1
    return arrays


def _product(values):
    total = 1
    for v in values:
        total *= v
    return total


def _gen_loops(rng):
    depth = rng.choice((1, 2, 2, 3, 3))
    loops, budget = [], MAX_ITERATIONS
    for var in LOOP_VARS[:depth]:
        lo = rng.randint(-3, 3)
        kind = rng.random()
        if kind < 0.1:
            trips = 0
        elif kind < 0.25:
            trips = 1
        else:
            trips = rng.randint(2, 5)
        trips = min(trips, max(budget, 1)) if trips else 0
        budget = budget // trips if trips else budget
        loops.append((var, lo, lo + trips - 1))
    return loops


def _subscript(rng, lower, upper, scope):
    """``(constant, coefficients, text)`` of a linear subscript that stays in ``lower..upper`` for every loop value."""
    size = upper - lower + 1
    coeffs = {v: rng.choice((-2, -1, 1, 1, 2, 3)) for v, _, _ in scope if rng.random() < 0.6}

    def span():
        low = high = 0
        for v, lo, hi in scope:
            c = coeffs.get(v, 0)
            hi = max(hi, lo)  # a zero-trip loop never runs; treat its range as the single value lo
            low += min(c * lo, c * hi)
            high += max(c * lo, c * hi)
        return low, high

    low, high = span()
    while high - low > size - 1:
        del coeffs[rng.choice(sorted(coeffs))]
        low, high = span()
    const = rng.randint(lower - low, upper - high)  # lower <= const + low and const + high <= upper
    terms = []
    for v, _, _ in scope:
        if v in coeffs:
            c = coeffs[v]
            terms.append((c, v if abs(c) == 1 and rng.random() < 0.8 else f"{abs(c)}*{v}"))
        elif rng.random() < 0.08:
            terms.append((1, f"0*{v}"))  # a written zero coefficient
    return const, coeffs, _subscript_text(const, terms)


def _subscript_text(const, terms):
    text = ""
    for c, body in terms:
        text += (" - " if text and c < 0 else " + " if text else "-" if c < 0 else "") + body
    if const or not text:
        text += (f" - {-const}" if const < 0 else f" + {const}") if text else str(const)
    return text


def _access(rng, arrays, scope):
    array = rng.choice(arrays)
    parts = [_subscript(rng, l, u, scope) for l, u in zip(array.lowers, array.uppers)]
    text = f"{array.name}[{', '.join(p[2] for p in parts)}]"
    subs = tuple(linear(const, **coeffs) for const, coeffs, _ in parts)
    return array.name, subs, text


def _gen_statement(rng, arrays, scope):
    """``(source text, oracle accesses in evaluation order)`` of one statement using only the loops in ``scope``."""
    kind = rng.choice(("sum", "sum", "store", "update"))
    reads = [_access(rng, arrays, scope) for _ in range(rng.randint(1, 3))]
    if kind == "update":
        array, subs, text = reads[0]
        return f"{text} = {text} + {rng.randint(1, 9)};", [read(array, *subs), write(array, *subs)]
    expr = reads[0][2]
    for _, _, text in reads[1:]:
        expr += f" {rng.choice('+-*')} {text}"
    if scope and rng.random() < 0.3:
        expr += f" + {scope[-1][0]}"  # a loop variable used as a value: no array access
    accesses = [read(array, *subs) for array, subs, _ in reads]
    if kind == "sum":
        return f"s = s + {expr};", accesses
    array, subs, text = _access(rng, arrays, scope)
    return f"{text} = {expr};", accesses + [write(array, *subs)]


def _gen_nest(rng, arrays, loops, depth, lines, indent):
    """Source lines and oracle items of the nest from loop ``depth`` on (statements may sit around inner loops)."""
    scope = loops[:depth]
    items = []

    def statements(count):
        for _ in range(count):
            text, accesses = _gen_statement(rng, arrays, scope)
            lines.append("  " * indent + text)
            items.append(Statement(accesses))

    if depth == len(loops):
        statements(rng.randint(1, 3))
        return items
    if depth and rng.random() < 0.25:
        statements(1)
    var, lo, hi = loops[depth]
    lines.append("  " * indent + f"for {var} = {lo} to {hi} do")
    inner = _gen_nest(rng, arrays, loops, depth + 1, lines, indent + 1)
    lines.append("  " * indent + "end")
    items.append(Loop(var, lo, hi, inner))
    if depth and rng.random() < 0.25:
        statements(1)
    return items


def generate_program(seed, index):
    """The program number ``index`` of ``seed``: same arguments, same program, on every run."""
    rng = random.Random(seed * 10000 + index)
    arrays = _gen_arrays(rng)
    loops = _gen_loops(rng)
    lines = [f"array {a.name}[{', '.join(f'{l}:{u}' for l, u in zip(a.lowers, a.uppers))}] at {a.base};" for a in arrays]
    lines.append("s = 0;")
    body = _gen_nest(rng, arrays, loops, 0, lines, 0)
    source = "\n".join(lines) + "\n"
    shape = {
        "dims": {len(a.lowers) for a in arrays},
        "depth": len(loops),
        "zero_trip": any(hi < lo for _, lo, hi in loops),
        "single": any(hi == lo for _, lo, hi in loops),
        "negative_coefficient": any(f"- " in line or "[-" in line or ", -" in line for line in lines),
    }
    return Program(seed, index, source, arrays, body, shape)


# -- checking ----------------------------------------------------------------------------------

def failure_message(program, findings):
    header = f"seed={program.seed} index={program.index}: {len(findings)} equivalence failure(s)"
    return (header + "\n" + "\n".join(f"  {f}" for f in findings)
            + f"\n--- program (seed={program.seed}, index={program.index}) ---\n{program.source}---")


def check_generated(testcase, program, oracle_trace=None):
    """Run ``program`` at all levels; fail ``testcase`` with seed, index and source on any disagreement."""
    trace = oracle.expected_trace(program.arrays, program.body) if oracle_trace is None else oracle_trace
    name = f"seed={program.seed} index={program.index}"
    results, findings = eq.check_program(program.source, trace, name)
    if findings:
        testcase.fail(failure_message(program, findings))
    return results, trace


class GeneratorTests(unittest.TestCase):
    def test_programs_are_deterministic(self):
        for seed in SEEDS:
            for index in (0, 17, 59):
                a, b = generate_program(seed, index), generate_program(seed, index)
                self.assertEqual((a.source, a.arrays, oracle.expected_trace(a.arrays, a.body)),
                                 (b.source, b.arrays, oracle.expected_trace(b.arrays, b.body)))

    def test_different_seeds_and_indexes_give_different_programs(self):
        sources = {generate_program(seed, i).source for seed in SEEDS for i in range(PROGRAMS_PER_SEED)}
        self.assertGreaterEqual(len(sources), 295)  # 300 programs, practically all distinct

    def test_oracle_accepts_every_program_so_all_subscripts_are_in_bounds(self):
        for seed in SEEDS:
            for index in range(PROGRAMS_PER_SEED):
                program = generate_program(seed, index)
                with self.subTest(seed=seed, index=index):
                    oracle.expected_trace(program.arrays, program.body)  # raises OracleError for an out-of-bounds subscript

    def test_generator_covers_the_required_shapes(self):
        shapes = [generate_program(s, i).shape for s in SEEDS for i in range(PROGRAMS_PER_SEED)]
        self.assertEqual(set().union(*(p["dims"] for p in shapes)), {1, 2, 3, 4})
        self.assertEqual({p["depth"] for p in shapes}, {1, 2, 3})
        for key in ("zero_trip", "single", "negative_coefficient"):
            self.assertGreaterEqual(sum(p[key] for p in shapes), 10, key)

    def test_arrays_never_overlap_and_have_one_to_four_dimensions(self):
        for seed in SEEDS:
            for index in range(PROGRAMS_PER_SEED):
                program = generate_program(seed, index)
                regions = sorted((a.base, a.base + a.size - 1) for a in program.arrays)
                self.assertTrue(all(prev[1] < nxt[0] for prev, nxt in zip(regions, regions[1:])), program.source)
                self.assertTrue(all(1 <= len(a.lowers) <= 4 for a in program.arrays))


class FailureMessageTests(unittest.TestCase):
    def test_failure_names_seed_index_and_source(self):
        program = generate_program(303, 7)
        trace = oracle.expected_trace(program.arrays, program.body)
        wrong = [("R", 999999)] + list(trace)
        with self.assertRaises(AssertionError) as caught:
            check_generated(self, program, wrong)
        message = str(caught.exception)
        self.assertIn("seed=303 index=7", message)
        self.assertIn("address trace vs oracle", message)
        self.assertIn(program.source, message)
        self.assertIn("differs at access 1: oracle (R, 999999)", message)


class RandomDifferentialTests(unittest.TestCase):
    def run_seed(self, seed):
        for index in range(PROGRAMS_PER_SEED):
            program = generate_program(seed, index)
            with self.subTest(seed=seed, index=index):
                results, trace = check_generated(self, program)
                self.assertEqual(list(results), list(eq.LEVELS))

    def test_seed_101(self):
        self.run_seed(101)

    def test_seed_202(self):
        self.run_seed(202)

    def test_seed_303(self):
        self.run_seed(303)

    def test_seed_404(self):
        self.run_seed(404)

    def test_seed_505(self):
        self.run_seed(505)

    def test_program_count(self):
        self.assertEqual(len(SEEDS) * PROGRAMS_PER_SEED, 300)

    def test_run_time_is_suitable_for_the_normal_suite(self):
        start = time.perf_counter()
        for index in range(PROGRAMS_PER_SEED):
            check_generated(self, generate_program(SEEDS[0], index))
        self.assertLess(time.perf_counter() - start, 30.0)  # one seed's 60 programs; about a second in practice


if __name__ == "__main__":
    unittest.main()
