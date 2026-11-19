# Design decisions

Conventions fixed in the roadmap (Section 3.1). Add one dated entry per later decision.

Provenance tags: **[PAPER]** stated in the paper. **[SIMPLIFIED]** a simplification chosen so the project fits a college course. **[OUR EXTENSION]** our own elaboration; the paper gives no algorithm for it.

## Decision log

### 2026-11-19 - Phase 0 baseline decisions

| # | Decision | Tag | Reason |
|---|---|---|---|
| 1 | Python 3.10 or newer | - | Modern syntax and type hints; the environment uses 3.13. |
| 2 | Standard library only (`unittest`, no third-party packages) | [SIMPLIFIED] | Reproducible; nothing to install. Heavy frameworks and LLVM are out of scope. |
| 3 | Quadruple intermediate code `(op, arg1, arg2, result)` | [PAPER] form | The paper's intermediate code is quadruples. |
| 4 | Loops kept structured as `FOR v lo hi` ... `ENDFOR v` instead of labels and jumps | [SIMPLIFIED] | Hoisting needs a clear "just before the loop" position. |
| 5 | Static arrays only: constant integer bounds, 1 to 4 dimensions | [PAPER] scope | The paper excludes variable-bound arrays; so do we. |
| 6 | Row-major storage, one storage unit per element | [PAPER] | Eq. 1. |
| 7 | Counted loops `for v = lo to hi`, constant bounds, step +1 | [SIMPLIFIED] | Smaller input language. |
| 8 | `di = ui - li + 1` | Our resolution | The paper prints both `-1` (Section 2) and `+1` (Section 3.1); `+1` is the correct count. |
| 9 | Ordinary notation `A[i1,...,in]` with `stride_k = d(k+1)...dn` | Our notation | Same quantity as the paper's reversed-index `Δ`. |
| 10 | Every quadruple tagged `ADDR` (address computation) or `BODY` (everything else) | [SIMPLIFIED] | Counters can show the effect on address arithmetic alone. |
| 11 | Measure instruction counts in our interpreter, not compile time | [SIMPLIFIED] | The paper measured compile time inside QEMU 1.7.2; we do not reproduce those experiments. |
| 12 | Only linear subscripts are optimized; others fall back to L1 and still run correctly | [PAPER] scope | The paper treats only linear subscripts. |
| 13 | Coding conventions: PEP 8 naming, docstrings on every module and public function, type hints on signatures | - | Roadmap Phase 0.7. |

## The four implementation levels

| Level | Name | How `A[i1,...,in]` is addressed at run time | Origin |
|---|---|---|---|
| L0 | Naive Eq. 1 | Subtract every lower bound at run time, combine with the nested multiply-add form, add base `a` at run time. Nothing precomputed. **Unoptimized baseline.** | [SIMPLIFIED] |
| L1 | CONSPART + VARPART | Lower-bound and base terms folded into CONSPART at compile time; run time evaluates only VARPART; access through `CONSPART[T]`. Also the fallback for non-linear subscripts. | [PAPER] Eq. 2, Section 3 |
| L2 | Linear-form address | Subscripts recognised as linear (Eq. 4); address rewritten as `K + Σ Ds·es` with `K` and every `Ds` computed at compile time. | [PAPER] Eq. 4-5 |
| L3 | Hoisted + incremental | From L2: partial sums depending only on outer loop variables computed in the outer loop; address updated by adding the constant `Ds` once per iteration. | [OUR EXTENSION] from the paper's Section 1 principles |

Expect L1 and L2 to be close for simple accesses such as `A[i,j]`; L2 gains where subscripts carry constants or coefficients. Report whatever is measured.

## What comes from where

**Paper concepts:** row-major address (Eq. 1); `CONSPART + VARPART` (Eq. 2); array information vector (Table 1); indexed access quadruples `=[ ]` and `[ ]=`; syntax-directed translation of subscript lists (Section 3.2); linear subscripts (Eq. 4); `D0`, `Ds` (Eq. 5); the four guiding principles of Section 1.

**Simplifications:** MiniLoop language; counted loops with constant bounds and step 1; structured `FOR`/`ENDFOR`; ADDR/BODY tags; instruction-count metric; reference interpreter; flat integer memory.

**Our extensions:** the concrete hoisting and incremental-update scheme (L3); the profitability guard; the optimization report; the naive L0 baseline definition; the name `K`. The paper gives no pseudo-code for the optimization algorithm.

**Honesty rules:** we do not claim to reproduce the paper's experiments, do not quote its numbers as our results, and every number in the final report must come from running our own program.

## Other fixed conventions

- **Address operand:** `BASE[T]`, effective address `BASE + value(T)`. For L0, `BASE = 0` and `T` is the full address; for L1-L3, `BASE = K` or `CONSPART`.
- **Memory model:** one flat integer memory; array `A` occupies `a ... a + size - 1`; scalars live in named cells outside array memory.
- **Counting rules:** `FOR` counted once per entry of the loop; `ENDFOR` once per iteration; every other quadruple once per execution. Multiplications, additions/subtractions, loads and stores are also counted separately.
- **Names:** package `arraypeep`; MiniLoop source files use the extension `.ml`.
