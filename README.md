# ArrayPeep

A Python implementation of array-address peephole optimization for compiler design.

*Sub-title: a simplified reproduction of the subscript-address optimization of Guan et al. (2025), with instruction-count evaluation.*

> **Status:** Phase 0 (project setup) complete. No compiler functionality is implemented yet.

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

## Basic setup

```
git clone https://github.com/khushiiii4/ArrayPeep.git
cd ArrayPeep
python --version
```

`python --version` must show 3.10 or newer. Nothing needs to be installed; a virtual environment is optional.

## Testing

Run from the project root:

```
python -m unittest discover -s tests -v
```

## Project layout

See Section 4 of the implementation roadmap. In short: `arraypeep/` (package), `tests/`, `examples/`, `benchmarks/`, `tools/`, `results/`, `docs/`.

Documentation so far: [notation cheat-sheet](docs/notation_cheatsheet.md), [design decisions](docs/design_decisions.md).
