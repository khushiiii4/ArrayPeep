# Test report - Phase 11 (correctness testing)

Date: 2026-11-20. Branch `main` at `9e34a6b` plus the uncommitted Phase 11 files (`tests/` and `docs/` only; no production code changed).

## 1. Result summary

| Item | Result |
|---|---|
| Strict suite `python -W error -X dev -m unittest discover -s tests` | **1106 tests, OK** (18 s; 980 before Phase 11, 126 added) |
| Examples runner `python tools/run_examples.py` | **5 examples x 5 levels = 25 runs, 25 passed, 0 failed** |
| T01-T18 at L0, L1, L2, hoist and L3 | **18/18 pass** (90 program-level runs) |
| Random differential test | **300 programs, 5 seeds, all pass** |
| Mutation check | **8 of 8 deliberate faults detected** (see Section 6) |
| Compiler defects found | **None** (`bug_log.md`) |
| `git diff --check` | clean |

New Phase 11 test modules and their test counts:

| Module | Tests | Purpose |
|---|---|---|
| `tests/test_oracle.py` | 43 | the independent oracle (11.1) |
| `tests/test_equivalence.py` | 19 | the three-way equivalence harness (11.2) |
| `tests/test_end_to_end.py` | 32 | T01-T18 (11.3) and edge cases (11.4) |
| `tests/test_robustness.py` | 19 | non-linear fallback, syntax and semantic errors (11.5) |
| `tests/test_random_differential.py` | 13 | 300 generated programs (11.6) |

## 2. Approach

### 2.1 Independent oracle (`tests/oracle.py`)

The oracle takes a plain description of a program (arrays with bounds and base, loops with constant ranges, statements as ordered lists of reads and writes, subscripts as integers, loop-variable names or functions) and produces the expected ordered `(R|W, address)` trace by looping in plain Python. It applies Eq. 1 directly, `D = a + SUM (i_k - l_k) * stride_k`, with no CONSPART/VARPART split and no precomputed constants. It also re-implements the initial memory pattern `(31x + 7) mod 1009`.

Independence is enforced, not assumed (`test_oracle.IndependenceTests`):

1. an AST walk of `oracle.py` (including imports inside functions) finds no `arraypeep` import and no relative import;
2. every import is from the standard library, and there is no `__import__`, `importlib`, `exec` or `eval`;
3. a fresh `python -I` process started in an unrelated directory imports the oracle and finds no `arraypeep` module in `sys.modules`.

The oracle itself is checked against hand-calculated values: the roadmap examples (1022, 504, 200), first and last elements, the T04 trace, the 1d_sum and 2d_traversal traces, and Eq. 1 against the CONSPART + VARPART split computed inside the test for every element of several arrays.

### 2.2 Three-way equivalence (`tests/equivalence.py`)

Every program is run at L0, L1, L2, hoist and L3 through the public driver (`run_source`, levels as in `arraypeep.__main__.LEVEL_RUNS`). For every level other than L0 the harness reuses `driver.compare_runs` to compare **final scalars**, the **complete memory image** and the **ordered address trace** with L0, and it compares the **trace of every level, L0 included, with the oracle trace**. The last comparison is what catches a mistake shared by all levels. A failure names the program, the level, the comparison and the first difference, for example `T03.ml: L3: scalars: scalar s differs: L0 101387 vs L3 147400`. A run-time error at one level becomes a finding for that level only.

On top of equivalence, `test_end_to_end.py` computes the expected final scalars and memory from the oracle's initial-memory formula in plain Python (a sum of initial values for the read-only programs; C = A + B for T10; Z = X*Y for T11; W[i] = 9 - i for T13).

## 3. T01-T18

Fixtures: `tests/cases/T<nn>.ml` and `tests/cases/T<nn>.expected.json` (K and D per access, decision, trace summary, scalar rule). Each row was run at **L0, L1, L2, hoist and L3**; scalars, memory and traces agree with L0 and the oracle at every level. K, D, the decision and the hoisted loops were checked against the L3 optimization report.

| Test | Program | Access: K ; D | Decision (L3 report) | Trace | Verdict |
|---|---|---|---|---|---|
| T01 | `V0[i]`, i = 0..9 | 100 ; i:1 | kept at L2 | 10, R100..R109 | PASS |
| T02 | `V5[i]`, i = 5..14 | 495 ; i:1 | kept at L2 (derived) | 10, R500..R509 | PASS |
| T03 | `A[i,j]`, 10 x 20 | 979 ; 20, 1 | L3 applied | 200, R1000..R1199 | PASS |
| T04 | `A[2i+1, j-1]` | 998 ; 40, 1 | L3 applied | 76, R1040..R1178 | PASS |
| T05 | `M[i,j,k]`, 3-D | 0 ; 30, 6, 1 | L3 applied (derived) | 120, R0..R119 | PASS |
| T06 | `N[i,j]`, negative bounds | 238 ; 7, 1 | L3 applied (derived) | 77, R200..R276 | PASS |
| T07 | `x = A[3,4]`, no loop | 1043 ; none | constant address, no run-time arithmetic | 1, R1043 | PASS |
| T08 | `A[i,5]` in i, j loops | 984 ; i:20, j:0 | L3 applied, hoisted out of the j loop | 30, R1004..R1184 | PASS |
| T09 | `S[j,i]`, transposed | 291 ; i:1, j:8 | L3 applied (derived) | 64, R300, R308, R316, ... R363 | PASS |
| T10 | `C[i,j] = A[i,j] + B[i,j]` | -6, 94, 194 ; 5, 1 each | L3 applied for all three (derived) | 60, R0 R100 W200 ... W219 | PASS |
| T11 | Z = X*Y, 4 x 4 | X -5 ; 4,0,1. Y 11 ; 0,1,4. Z 27 ; 4,1,0 | L3 applied; Z update hoisted out of the k loop | 272, W32 ... W47 | PASS |
| T12 | `V[i*j]` | none | NOT LINEAR (product of loop variables), fallback to L1 | 100, R0..R81 | PASS |
| T13 | `W[i] = 9-i`; `V[W[i]]` | W[i] write 0 ; i:1 | write kept at L2; `V[W[i]]` NOT LINEAR (array element in subscript), fallback; nested `W[i]` inside the fallback window | 30, W0 ... R10 | PASS |
| T14 | `A[i+k, 1]`, scalar k | none | NOT LINEAR (variable that is not a loop variable), fallback | 5, R1040..R1120 | PASS |
| T15 | `for i = 5 to 1` | 980 ; i:20 | kept at L2 (derived); all levels identical | 0 (empty) | PASS |
| T16 | `U[1:1, 1:5]`, i = 1..1 | 44 ; 5, 1 | L3 applied (derived) | 5, R50..R54 | PASS |
| T17 | `H[0:1,0:1,0:1,0:2]`, 4-D | 0 ; 12, 6, 3, 1 | L3 applied (derived) | 24, R0..R23 | PASS |
| T18 | `A[11-i, j]` | 1199 ; -20, 1 | L3 applied | 200, R1180, R1181, ... R1019 | PASS |

Notes.

- "(derived)": the roadmap's expected-decision column gives no decision for these rows. The expectation comes from the documented profitability guard (L3 only if the arithmetic per innermost iteration strictly drops; `quad_spec.md` 7, design decision 91) and is recorded in the `decision_basis` field of the expected file. All K, D, trace and fallback values come from the roadmap or from hand calculation; none was adjusted to match compiler output.
- T11: the final Z is compared with X*Y recomputed in plain Python from the oracle's initial memory, at every level.
- T12 and T14: the L2, hoist and L3 listings are identical to L1 (nothing is optimizable).
- T07, T15 and T16 (degenerate shapes) run at all levels without error, as the roadmap requires.

## 4. Edge cases (task 11.4, `EdgeCaseTests`)

| Case | Tests |
|---|---|
| Zero-trip loop | inner loop with a statement after it; outer loop; negative range (`-1 to -3`); T15 |
| Single-iteration loop | outer and inner (`7 to 7`, `4 to 4`); inner inside a longer outer loop; T16 |
| Dimension of size 1 | middle dimension of `G[1:3,1:1,1:4]`; one-element 1-D array; 1 x 1 array; T16 |
| Negative array bounds | array and loops entirely negative (`R[-10:-5,-8:-2]`); descending subscript `Q[-i]` on `Q[-4:4]`; T06 |
| Negative and zero coefficients | `A[12-2i, 3+0*j]` (D = -40, 0); `A[i, 21-j]`; coefficients 3 and 2; T18 |
| Access independent of a loop | independent of the inner loop (hoisted); of the middle and the outer loop; T08 |
| Same element twice in one statement | `A[i,j] + A[i,j]`; `V[i] = V[i]*V[i]`; `A[i,j] = A[i,j] + 1` (expected memory computed independently) |
| 4-D array | permuted subscripts; negative bounds; reversed linear subscripts; T17 |

## 5. Robustness (task 11.5, `test_robustness.py`)

- **Non-linear subscripts fall back and stay correct** (all levels equal L0 and the oracle; reason checked; "kept at L1"): `i*j`, `i*i`, `2*(i*j)+1`, `A[i, j*j]` (one non-linear subscript makes the whole access fall back); indirect subscripts `A[W[i], j]`, `V[W[i]] = i` on an assignment target, `V[W[U[i]]]`; scalars assigned before the loop, changed in the loop (`k = k + 1`) and computed from the loop variable (`k = 2*i`); a program mixing a linear access (still optimized) with a fallback access; one program with all three reasons.
- **Syntax and semantic errors** are reported through the real command line (exit code 1, one line on standard error, nothing on standard output, the position and message of `language_spec.md` Sections 5 and 9): the six syntax examples of the spec (missing `;`, `]`, `do`, `end`; non-literal bounds; illegal character), S1-S10 and S12 with one sample each, S11 (reported as S10), the first-error-only rule and syntax-before-semantic. The rule-by-rule variations stay in `test_lexer.py`, `test_parser.py` and `test_semantic.py`; a guard test checks that those classes still exist.

## 6. Random differential test (task 11.6)

Configuration (`tests/test_random_differential.py`):

| Parameter | Value |
|---|---|
| Programs | **300** = 5 seeds x 60 |
| Seeds | **101, 202, 303, 404, 505** |
| Program stream | `random.Random(seed * 10000 + index)`; program `(seed, index)` reproduces from `generate_program(seed, index)` alone |
| Arrays | 1-3 per program, 1-4 dimensions, random bounds (lower -4..4, size 1..6 per dimension, at most 300 cells), random non-overlapping bases |
| Loops | nests of 1-3 loops, random ranges, about 10 % zero-trip and 15 % single-iteration, at most 100 iterations per nest |
| Subscripts | random linear subscripts (coefficients -2..3, some written `0*j`) with a constant chosen so every value stays in bounds; the oracle raises on any out-of-bounds access and none did |
| Statements | sums into a scalar, stores, in-place updates; 1-3 per body, sometimes before or after an inner loop |
| Checks | all five levels; scalars, full memory and ordered trace against L0; trace against the oracle |
| Failure message | seed, index, every finding, and the complete program source |

Measured over the 300 programs: 254 have a non-empty trace (18,573 accesses in total); programs containing arrays of 1/2/3/4 dimensions: 184/161/72/82; nest depth 1/2/3: 78/124/98; L3 report entries: 706 constant-address, 525 kept at L2, 459 with L3 applied. Run time: about 4 s for the 300 programs (the test file takes about 6 s). Result: all 300 pass at all five levels.

## 7. Mutation sanity check (task 11.7)

Method: each fault was applied by exact string replacement to a **temporary copy** of the project (`.git`, `__pycache__` and `results` left out), the targeted tests were run in that copy (the copy's own `arraypeep` was imported, which the experiment checked), and the copy was deleted. The real tree was hashed (`arraypeep/*.py`, `tests/*.py`) before and after the experiment and was unchanged.

Command, run in the copy's root (245 tests):

```
python -W error -X dev -m unittest tests.test_end_to_end tests.test_random_differential tests.test_robustness tests.test_precompute tests.test_loop_opt
```

Control (unmodified copy): 245 tests, OK.

| ID | Mutation | File / function | Outcome (distinct failing tests, subtests collapsed) | Example failure |
|---|---|---|---|---|
| M1 | one `D_s` coefficient changed by +1 (innermost loop variable) | `arraypeep/precompute.py`, `make_plan` | **detected**: 131 (test_precompute 27, test_loop_opt 67, test_end_to_end 26, test_random_differential 6, test_robustness 5) | `T01.ml: L2: run: RuntimeAccessError ... effective address 110 is outside every declared array` |
| M2 | sign of every `D_s` flipped | `precompute.py`, `make_plan` | **detected**: 140 (test_precompute 26, test_loop_opt 78, test_end_to_end 25, test_random_differential 6, test_robustness 5) | `T01.ml: L2: run: RuntimeAccessError ... effective address 99 ...` |
| M3 | incremental update skipped (`P = P + D` becomes `P = P + 0`) | `arraypeep/loop_opt.py`, `increment_l2` | **detected**: 71 (end_to_end 19, random 6, robustness 1, loop_opt 45); `test_precompute` correctly stays green (L3 only) | `T03.ml: L3: scalars: scalar s differs: L0 101387 vs L3 147400` |
| M4 | incremental update altered (`P = P + D + 1`) | `loop_opt.py`, `increment_l2` | **detected**: 71 (loop_opt 45, end_to_end 19, random 6, robustness 1) | `T03.ml: L3: run: RuntimeAccessError ... effective address 1200 ...` |
| M5 | lower bound dropped from the initial address (`D_r * lo` becomes 0) | `loop_opt.py`, `increment_l2` | **detected**: 67 (loop_opt 42, end_to_end 18, random 6, robustness 1) | `T03.ml: L3: run: RuntimeAccessError ... effective address 979 ...`; `seed=505 index=50: L3: scalars: scalar s differs: L0 4500 vs L3 3880` |
| M6 | multiplication skipped for coefficient 2 (`if d != 1` becomes `if d != 2`) | `arraypeep/optimizer.py`, `emit_l2` | **detected**: 38 (precompute 16, loop_opt 15, random 6, end_to_end 1) - **but not by the T01-T18 matrix** (see below) | `test_end_to_end.EdgeCaseTests.test_coefficient_larger_than_one_and_zero_on_a_loop` |
| M7 | coefficient changed by -1 in the hoisted address (`D_s - 1`) | `loop_opt.py`, `hoist_l2` | **detected**: 77 (loop_opt 48, end_to_end 22, random 6, robustness 1) | `T03.ml: hoist: run: RuntimeAccessError ... effective address 999 ...` |
| M8 | constant K changed by +1 | `precompute.py`, `make_plan` | **detected**: 111 (precompute 27, loop_opt 47, end_to_end 26, random 6, robustness 5) | `T01.ml: L2: run: RuntimeAccessError ... effective address 110 ...` |

Result: **all 8 faults were detected, by the end-to-end, random and unit tests alike**; at least three were required. The three families asked for are covered: coefficient +1 / -1 (M1, M7), sign flip (M2), skipped or altered incremental update (M3, M4, M5). Each failure message identifies the affected program, level and behaviour (a scalar difference, or an access outside every declared array).

**Observation on M6.** The mutation was not caught by `MatrixTests` (T01-T18): none of the 18 programs has a coefficient `D_s = 2` (their D values are 0, 1, 3, 4, 5, 6, 7, 8, 12, 20, 30, 40 and -20), so the missing multiplication never changes an address. It was caught by the edge-case test with coefficients 3 and 2, by the random test and by the Phase 8 unit tests. This is a gap in the roadmap's matrix, not in the combined suite; it is why the edge cases and the random test are kept in addition to T01-T18.

## 8. Limitations and unresolved issues

- **Roadmap decision column:** for T02, T05, T06, T09, T10, T15, T16 and T17 the roadmap states no expected decision; the tests use the guard rule instead (Section 3). If the intended decision for any of them differs, that expected file needs a one-word change.
- **Oracle scope:** the oracle models addresses only. It has no memory model, so a subscript that depends on stored data (T13 and the indirect cases) is described by hand with a function that encodes what the program stores (`9 - i`). Scalars and memory contents are checked against L0 and, for the matrix cases, against values computed in the test; for the random programs only against L0.
- **Random generator scope:** linear subscripts only (non-linear fallback is covered by the hand-written tests), one scalar accumulator, one loop nest per program, constant bounds and step 1 (the language has no others), at most 300 cells per array. Because the initial pattern repeats every 1009 addresses, a wrong-address read between arrays 1009 or more apart could in principle return the same value; the trace check does not have that weakness, which is why it is the primary check.
- **Mutation check is a documented procedure**, not part of the default suite. The eight replacements are listed above (file, function and exact change) so they can be repeated on a copy; no mutation script is committed.
- **Oracle row in the CLI:** `quad_spec.md` 9.4 shows an `oracle` row in the verification block. The command line still prints only the L0-vs-level rows (`report.py` says so); Phase 11's tasks do not ask for it, and it is not implemented.
- **Clean-clone run:** the roadmap suggests running the suite from a clean clone. The Phase 11 files are not committed yet, so a clone of the repository would not contain them; the suite was run in the working tree and in 9 copies of it (the mutation experiment, including the unmutated control).
- **Phase 12 items** (improvement ratios, scaling tables, CSV output) are not part of this phase and were not started.
