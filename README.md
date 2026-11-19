# ArrayPeep

A Python implementation of array-address peephole optimization for compiler design.

*Sub-title: a simplified reproduction of the subscript-address optimization of Guan et al. (2025), with instruction-count evaluation.*

> **Status:** the full pipeline (lexer, parser, semantic checks, L0/L1 code, L2/hoist/L3 optimization, interpreter, verification) and the command line are implemented.

## Research paper

| Item | Detail |
|---|---|
| Title | *Peephole Optimization Strategies for Intermediate Code in the Compilation Process* |
| Authors | Yunxin Guan (corresponding author), Jing Yun, Pengfei Sun, Xiaoming Su |
| Published in | *Intelligent Manufacturing and Cloud Computing*, eds. I.S. Jesus and K. Wang, IOS Press, 2025, pp. 107-112 (open access, CC BY-NC 4.0) |

## Project description

In row-major storage the address of an element of an n-dimensional array depends on all n subscripts, and inside nested loops that address is recomputed on every iteration. The paper shows that much of this work can be removed: some parts are compile-time constants, some do not change in inner loops, and the rest changes by a regular step.

ArrayPeep translates a small loop language (MiniLoop, `.ml` files) with statically declared arrays into quadruple intermediate code, applies the paper's address optimization, and runs both versions in a reference interpreter so that correctness and the number of executed address operations can be compared.

## Main objective

Implement the paper's array-address model (CONSPART + VARPART factorisation, linear subscripts, precomputed coefficients) on quadruple code, prove the optimized code equivalent to the unoptimized code by execution, and measure the saving with the project's own instruction counters.

This project does **not** reproduce the paper's QEMU compile-time experiments; it measures instruction counts in a small interpreter.

## Technology used

- Python 3.10 or newer (developed with Python 3.13)
- Python standard library only (no third-party packages)
- `unittest` for testing
- Git / GitHub for version control

## Setup

Requires Python 3.10 or newer (developed with 3.13) and nothing else: the project uses the standard library only, so there is nothing to install.

```
git clone https://github.com/khushiiii4/ArrayPeep.git
cd ArrayPeep
python --version
```

## Usage

Run from the repository root:

```
python -m arraypeep FILE [--level LEVEL] [--show-vectors] [--show-quads] [--show-plan] [--run] [--compare]
```

`FILE` is a MiniLoop source file (see `examples/`).

| Option | Meaning |
|---|---|
| `--level LEVEL` | Level to generate and run: `L0`, `L1`, `L2`, `hoist` (hoisting only), `L3`, or `all` (those five in order). Default `L3`. |
| `--show-vectors` | Print the array information vectors. |
| `--show-quads` | Print the quadruple listing of every selected level. |
| `--show-plan` | Print the optimization report (levels `L2`, `hoist`, `L3`; `all` shows the `L3` one). |
| `--run` | Print the execution result (counters, final scalars, memory checksum) of every selected level. This is the default when no `--show-*` flag is given. |
| `--compare` | Verify that every level matches `L0` (requires `--level all`). |

Examples:

```
python -m arraypeep examples/matrix_multiply.ml
python -m arraypeep examples/2d_traversal.ml --level all --compare
python -m arraypeep examples/1d_sum.ml --level L2 --show-quads --show-plan
```

### Verification

With `--compare`, every level is compared with `L0` on three checks: the final scalars, the final memory image and the complete ordered address trace. Instruction counters are not compared. A verification block is printed with one PASS/FAIL line per level and an overall verdict.

### Exit codes

Every error prints exactly one line to standard error and never a traceback.

| Code | Meaning |
|---|---|
| 0 | Success (and, with `--compare`, every level matched `L0`). |
| 1 | Unreadable file, syntax, semantic or declaration error, or invalid command line. |
| 2 | Run-time error while executing the program (access outside every declared array, step limit exceeded). |
| 3 | Verification failed: a level disagrees with `L0`. |
| 4 | Internal error (a compiler bug: ill-formed generated code or an unexpected exception). |

### Examples runner

```
python tools/run_examples.py
```

Runs every `examples/*.ml` at `L0`, `L1`, `L2`, `hoist` and `L3`, prints one `PASS`/`FAIL` line per example and level plus a total, and exits 0 only if every run passed (1 otherwise, also when no example is found).

## Testing

Run from the project root. The strict run turns warnings into errors:

```
python -W error -m unittest discover -s tests
```

Add `-v` for a verbose listing.

## Project layout

See Section 4 of the implementation roadmap. In short: `arraypeep/` (package), `tests/`, `examples/`, `benchmarks/`, `tools/`, `results/`, `docs/`.

Documentation so far: [notation cheat-sheet](docs/notation_cheatsheet.md), [design decisions](docs/design_decisions.md).
