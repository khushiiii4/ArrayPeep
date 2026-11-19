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
| 8 | `di = ui - li + 1` | [PAPER] (the paper itself is inconsistent; we resolve it) | The paper prints both `-1` (Section 2) and `+1` (Section 3.1); `+1` is the correct count. |
| 9 | Ordinary notation `A[i1,...,in]` with `stride_k = d(k+1)...dn` | [SIMPLIFIED] (notation only; same quantity as the paper's `Δ`) | Same quantity as the paper's reversed-index `Δ`. |
| 10 | Every quadruple tagged `ADDR` (address computation) or `BODY` (everything else) | [SIMPLIFIED] | Counters can show the effect on address arithmetic alone. |
| 11 | Measure instruction counts in our interpreter, not compile time | [SIMPLIFIED] | The paper measured compile time inside QEMU 1.7.2; we do not reproduce those experiments. |
| 12 | Only linear subscripts are optimized; others fall back to L1 and still run correctly | [PAPER] scope | The paper treats only linear subscripts. |
| 13 | Coding conventions: PEP 8 naming, docstrings on every module and public function, type hints on signatures | - | Roadmap Phase 0.7. |

### 2026-11-19 - Phase 1 decisions (language, quadruples, memory, outputs)

Specifications: `language_spec.md`, `quad_spec.md`, `hand_examples.md`. Numbering continues from the Phase 0 table.

| # | Decision | Tag | Reason / where |
|---|---|---|---|
| 14 | Array declaration `array A[l1:u1, ..., ln:un] at BASE;`; bounds and `BASE` are integer constants (optionally negative); `u >= l`; 1 to 4 dimensions | [PAPER] scope (static arrays); syntax [SIMPLIFIED] | Roadmap Phase 1 table, Section 2.3. Explicit `BASE` keeps tests deterministic. `language_spec.md` 4.1 |
| 15 | Counted loop `for v = lo to hi do ... end`; constant bounds; step +1; `lo > hi` is a legal zero-trip loop | [SIMPLIFIED] | Constant bounds and step 1 make linear subscripts analysable. `language_spec.md` 4.5 |
| 16 | Expressions: integer literals, scalars, array elements, `+`, `-`, `*`, parentheses, unary minus; precedence parentheses > unary `-` > `*` > `+ -`; left-associative; any expression may be a subscript, only linear ones are optimised | [SIMPLIFIED] / [PAPER] (subscript lists) | Roadmap Phase 1 table. `language_spec.md` 3, 4.4 |
| 17 | Subscript lists parsed with an iterative list (`expr { "," expr }`) instead of the paper's left-recursive `elist -> elist , E` | [PAPER]-derived | Same language, no left recursion (Phase 3.4). The paper's own grammar text is not reproduced; we use the roadmap's description of `G[A]`, `G[V]` |
| 18 | Declarations only at the top level; array must be declared before first use; no `+=`, no print statement, no `if`/`while`/functions | [SIMPLIFIED] | `language_spec.md` 3, 5 |
| 19 | Scalars need no declaration and start at 0 | [OUR EXTENSION] | Roadmap silent. `language_spec.md` 4.2 |
| 20 | A loop variable is local to its loop and its name is reserved for loop variables (cannot also be a scalar or array name); sequential loops may reuse a name; nested loops may not | [OUR EXTENSION] | Roadmap only forbids assigning a loop variable inside its loop and reusing a name in nested loops. Stricter rule keeps loop variables unambiguous for the optimiser (rules S8-S11) |
| 21 | Identifiers matching `[TP][0-9]+` are reserved for generated temporaries (`T` and `P` alone are ordinary names) | [OUR EXTENSION] | Prevents clashes with listings. Rule S12 |
| 22 | Evaluation order: operands left to right; for `A[..] = E;` the right-hand side is evaluated first, then the target's subscripts, then the store | [OUR EXTENSION] | Roadmap silent; needed so the address trace is well defined. `language_spec.md` 4.4 |
| 23 | Exactly eight opcodes: `+`, `-`, `*`, `:=`, `=[ ]`, `[ ]=`, `FOR`, `ENDFOR` | [PAPER] form for `=[ ]`, `[ ]=`; rest [SIMPLIFIED] | `quad_spec.md` 3 |
| 24 | Unary minus is emitted as `(-, 0, x, T)` and counted as a subtraction; no constant folding | [OUR EXTENSION] | The roadmap lists no unary opcode. `quad_spec.md` 3 |
| 25 | Field roles of loop markers: `FOR`: arg1 = variable, arg2 = lo, result = hi; `ENDFOR`: arg1 = variable | [OUR EXTENSION] (reading of roadmap's `FOR v lo hi`) | The roadmap prints the listing but does not name the fields. `quad_spec.md` 3 |
| 26 | Exact loop semantics: `FOR` enters (or skips to after `ENDFOR` when `lo > hi`); `ENDFOR` increments and jumps back unless `v = hi`; `FOR` counted once per entry, `ENDFOR` once per iteration; dynamic count `1 + N(k+1)` | [SIMPLIFIED] (counting rule from roadmap Section 3.1) | `quad_spec.md` 3.1, 8 |
| 27 | Temporaries `T<n>` are statically single-assigned; L3 uses separate address temporaries `P<n>` that are updated in place | [OUR EXTENSION] | Roadmap's L3 figure updates `p2 += Dj` while its Phase 4 test forbids assigning a temporary twice; the two kinds resolve this. `quad_spec.md` 2 |
| 28 | Indexed operand `BASE[X]`, effective address `BASE + value(X)`; constant address written `K[0]`; L0 uses `0[T]` | [PAPER] (indexed access with constant base) / [SIMPLIFIED] | `quad_spec.md` 2 |
| 29 | Tags: the address arithmetic is `ADDR`; the indexed access quadruple itself, `FOR`/`ENDFOR` and user arithmetic are `BODY` | [SIMPLIFIED]; the access-is-BODY reading is [OUR EXTENSION] | Keeps "dynamic ADDR" to address arithmetic only. `quad_spec.md` 6 |
| 30 | Memory: one flat integer memory, one cell per element, scalars outside it, arrays occupy `a .. a+size-1`, regions may not overlap | [PAPER] (one storage unit per element) / [SIMPLIFIED] | `quad_spec.md` 4 |
| 31 | Run-time check is on the effective address only: outside every declared array is an error. Per-subscript range errors that still land inside memory are not detected | [SIMPLIFIED] | Roadmap Phase 1.3 defines the check this way; the limitation is documented in `quad_spec.md` 4.3 |
| 32 | Initial memory contents `init(x) = (31*x + 7) mod 1009` for every cell of a declared array, identical for all levels | [OUR EXTENSION] | Roadmap Phase 6.1 asks for a fixed address-dependent pattern but does not specify one. May change only through a new decision |
| 33 | Run-time step limit, default 10,000,000 executed quadruples | [OUR EXTENSION] | Roadmap Phase 6.6 requires a limit, not its value |
| 34 | Observable behaviour = (final scalars, final memory image, ordered address trace); levels must agree on all three and on the oracle | [SIMPLIFIED] | Roadmap Section 5.4. `quad_spec.md` 4.6, 8 |
| 35 | Definition of L0 order: `E1`, `- l1`, then per further subscript `Ek`, `* dk`, `- lk`, `+`, finally `+ a`; always subtracts lower bounds, even 0 | [SIMPLIFIED] | Reproduces roadmap's Phase 5 figure; the roadmap's T04 prediction lists a different order with the same counts (documented) |
| 36 | L1 per the paper's PLACE/LIMIT scheme; L2 emission rules (skip `Ds = 0`, no multiplication for `Ds = 1`, `K[0]` for a constant address); L3 hoisting + incremental update with profitability guard | L1 [PAPER]; L2 [PAPER] maths + [OUR EXTENSION] emission; L3 [OUR EXTENSION] | `quad_spec.md` 7 |
| 37 | Output layouts: information vector, optimization report, verification, metrics as in roadmap Section 5.2 (header width 74); quadruple listing with a tag column; added `verdict` line | Layouts [SIMPLIFIED]; listing and verdict line [OUR EXTENSION] | `quad_spec.md` 9 |
| 38 | Errors: syntax `line L, col C`; semantic `line L`; run-time error names the quadruple; exit codes 0 / 1 (input, syntax, semantic, command line) / 2 (run-time) / 3 (verification FAIL) / 4 (internal error; added by decision 40) | Policy [SIMPLIFIED]; exit codes [OUR EXTENSION] | Roadmap Phase 1.8 asks only for "non-zero". `language_spec.md` 9, `quad_spec.md` 10 |

### 2026-11-19 - Phase 1 review fixes

Decisions taken after an independent review of the Phase 1 documents. They refine, and do not change, decisions 14-38.

| # | Decision | Tag | Reason / where |
|---|---|---|---|
| 39 | Command-line usage errors exit with code 1 (the driver overrides `argparse`'s default of 2) | [OUR EXTENSION] | `argparse` exits with 2, which would collide with "run-time error". `language_spec.md` 9 |
| 40 | Internal errors (ill-formed generated code) exit with code 4 and print `internal error: ...` | [OUR EXTENSION] | Exit code was undefined. `language_spec.md` 9, `quad_spec.md` 10 |
| 41 | Several levels in one run: a run-time error in L0 ends the run with exit 2; a run-time error in a later level while L0 completes is a verification FAIL (exit 3) | [OUR EXTENSION] | A later-level error means the optimiser is wrong, not the program. `quad_spec.md` 10 |
| 42 | Run-time and internal messages have no file name or source line, are one line on standard error, and name the level when several levels run | [OUR EXTENSION] | They refer to a quadruple, not a source position. `language_spec.md` 9, `quad_spec.md` 10 |
| 43 | Syntax-error position: an unexpected token is reported at its start; a missing token or an unexpected end of file just after the end of the previous token | [OUR EXTENSION] | The roadmap's example (`line 4, col 12: expected ';'`) needs this. `language_spec.md` 9 |
| 44 | Only the first semantic error is reported; when one construct breaks several rules, the lowest-numbered rule S1-S12 wins; a name declared as an array anywhere is an array name everywhere (S3) | [OUR EXTENSION] | Removes the contradiction "one message" / "all violations" and makes golden error tests deterministic. `language_spec.md` 5, 9 |
| 45 | Access window = contiguous run holding all `ADDR` quadruples of the access; a nested access in a subscript may lie inside it; the window of an optimisable access is exactly its `ADDR` quadruples | [SIMPLIFIED] | Roadmap 5.5 and 7.5 wording. `quad_spec.md` 6 |
| 46 | The L0 emission rule uses temporaries named `U`, `V`, `W` in its description (they are ordinary `T` temporaries); the L1 rule is ordered like L0 (code of `Ek`, then `PLACE * dk`, then `+ Ek`) | [SIMPLIFIED] (L0) / [PAPER] scheme, ordering [OUR EXTENSION] (L1) | Avoids a clash with the reserved `P<n>`; makes L1 listings reproducible. `quad_spec.md` 7.1 |
| 47 | The metrics block header is `== Metrics ===...`; the roadmap's "(layout only; ...)" text is not printed | [SIMPLIFIED] | `quad_spec.md` 9.5 |
| 48 | Source files are UTF-8 (BOM ignored), LF or CRLF line endings accepted, non-ASCII only inside comments, identifiers use ASCII letters, a tab is one column | [OUR EXTENSION] | The project is edited on Windows. `language_spec.md` 2 |
| 49 | The memory image is the contents of the declared array regions only; `init` is injective on any 1009 consecutive addresses and repeats beyond that, so the address trace stays the primary equivalence check | [OUR EXTENSION] | `quad_spec.md` 4.1, 4.4, 4.6 |

### 2026-11-19 - Phase 2 decisions (symbol table and address math)

Numbering continues from the Phase 1 review table. Roadmap Phase 2 gives the formulas; these decisions fix the choices it leaves open.

| # | Decision | Tag | Reason / where |
|---|---|---|---|
| 50 | Errors derive from one base class `ArrayPeepError`, defined for now in `symbol_table.py` (it may move to its own module in Phase 10). `address_math.py` stays dependency-free and raises the built-in `ValueError` (invalid bounds or sizes) and `TypeError` (non-integer input); `symbol_table.py` translates them into `ArrayPeepError` subclasses with the messages of `language_spec.md` Section 5 | [OUR EXTENSION] | Phase 10.5 wants one base class, but the Section 4 layout has no errors module. Keeps `address_math` testable alone (roadmap Phase 2 completion criterion) |
| 51 | The symbol table enforces S4 (duplicate array), S5 (`u >= l`), S6 (1 to 4 dimensions) and S7 (no overlap; adjacent regions are allowed), and registers scalars and loop variables with name-class conflicts. S8, S9, S11, S12 stay in `semantic.py` (Phase 3). Errors carry an optional line number so Phase 3 can attach it | [OUR EXTENSION] | Roadmap Phase 2.5 asks for arrays, scalars, loop variables, duplicates and overlaps; the scoping rules need the AST |
| 52 | A bound or base address that is not an `int` (including `bool`) is rejected with a message saying that variable-bound arrays are not supported | [PAPER] scope (static arrays only) | Roadmap Phase 2.6. The parser already rejects non-literal bounds as a syntax error; the table can only check types |
| 53 | `address_math.py` also provides `varpart` (nested form of Eq. 2) and `direct_address` (Eq. 1). Neither range-checks the subscripts, because the address identity holds for any integers and the run-time check is on the address (`quad_spec.md` 4.3). The tests keep their own independent product-form Eq. 1 | [OUR EXTENSION] | The identity test needs both sides; the test must not share code with the code under test |
| 54 | `address_math.py` works for any `n >= 1`; the limit of 4 dimensions is rule S6 of the symbol table, not of the math. It imports only the standard library | [SIMPLIFIED] | Pure formulas; roadmap 2.1 to 2.4 |
| 55 | Tests for `address_math` live in `tests/test_symbol_table.py`; no separate test file | [SIMPLIFIED] | Roadmap Phase 2 names only that test file |
| 56 | Git tags `phase-1` and `phase-2` are created only when the user approves, at the end of the phase. None exists yet | - | Roadmap Appendix B and the Phase 0 / Phase 10 checklists use phase tags |
| 57 | Information-vector printer, empty table: when no arrays are registered, `format_information_vectors()` returns the standard 74-character `== Array information vector ===...` header followed by a line ` (no arrays declared)`. The function returns a string without a trailing newline (printing is left to the caller) | [OUR EXTENSION] | `quad_spec.md` 9.1 defines the layout of one array's block only and says nothing about a program with no arrays. Implemented in `symbol_table.py` (`format_information_vectors`) |

### 2026-11-19 - Phase 3 and 4 decisions (front end and scalar code generation)

Numbering continues from decision 57. These choices implement the Phase 1 specifications; where a message or a rule was not fixed there, it is fixed here.

| # | Decision | Tag | Reason / where |
|---|---|---|---|
| 58 | Lexer: tokens are `Token(kind, text, line, col)` with a `TokenKind` enum; comments produce no token; the token list always ends with one `EOF` token; a leading byte-order mark is skipped without counting a column; a tab is one column; illegal characters give `illegal character 'X'` (non-printable ones shown escaped, e.g. `'\x00'`); digits and letters are ASCII only | [OUR EXTENSION] (rules from `language_spec.md` 2) | `lexer.py` |
| 59 | Parser position rule (decision 43) as implemented: a missing token (`expected ';'`, `expected ']'`, `expected 'do'`, ...) is reported just after the previous token; an unexpected token (`expected an expression`, `expected a statement`, stray `end`, a declaration inside a loop) at its own start; at end of file every message gets ` before end of file` and the position is just after the last token | [OUR EXTENSION] | `parser.py`; roadmap Phase 3 example `line 4, col 12: expected ';'` |
| 60 | Parser messages: `expected an array name`, `expected a loop variable name`, `expected an expression`, `expected a statement`, `unexpected 'end' (no matching 'for')`, `array declarations are only allowed at the top level`, `array bounds must be integer constants (variable-bound arrays are not supported)`, `the base address of an array must be an integer constant`, `loop bounds must be integer constants`, `expected an integer constant before end of file` | [OUR EXTENSION] | `language_spec.md` Section 9 fixes only some of them |
| 61 | AST nodes are frozen dataclasses; `line` and `col` are keyword-only and **excluded from equality**, so the round-trip test (parse, print, parse) compares shapes. `ArrayRef(name, subscripts)` holds the whole subscript list (the effect of the paper's rewritten grammar G[V]). Parentheses leave no node; `-5` is `UnaryMinus(Number(5))` (no folding). The pretty-printer uses the fewest parentheses that keep the tree (a right operand of equal precedence is parenthesised) | [PAPER]-derived (G[V]) / [SIMPLIFIED] | `ast_nodes.py`, roadmap 3.3, 3.7 |
| 62 | Error classes: `MiniLoopSyntaxError` (`lexer.py`, with `line` and `col`), `SemanticError` (`semantic.py`), `CodegenError` (`codegen.py`) all derive from `ArrayPeepError`; `format(filename)` renders the layouts of `language_spec.md` Section 9 | [OUR EXTENSION] | Decision 50 |
| 63 | Semantic analysis walks the AST in source order after a pre-pass that collects all array names and all loop-variable names, so the "anywhere" rules (S3, S8, S10) fire at the first offending use. It stops at the first violation; per construct the lowest-numbered rule wins (decision 44). S4 to S7 are delegated to the symbol table and its messages are reused; `analyze(program)` returns the filled `SymbolTable` | [OUR EXTENSION] | `semantic.py`, roadmap 3.5, 3.6 |
| 64 | **S11 is subsumed by S10.** A read of a loop-variable name outside its loop breaks both S10 and S11; decision 44 reports the lower number, so S10 (`'i' is used as a loop variable and as an ordinary name`) is what is reported and no input reaches an S11 message. This follows the approved text of `language_spec.md` Section 5 literally. If a dedicated message (`loop variable 'i' used outside its loop`) is preferred, the precedence paragraph of Section 5 must change first | [OUR EXTENSION] | Open point for review |
| 65 | `Quad(op, arg1, arg2, result, tag)` is a frozen record; operands are `int` (literal), `str` (name, `T<n>`, `P<n>`) or `None` (unused, printed `-`); opcodes allowed in Phase 4 are `+ - * := FOR ENDFOR` (Phase 5 adds `=[ ]` and `[ ]=`); the tag defaults to `BODY` and every Phase 4 quadruple is `BODY` | [PAPER] form / [SIMPLIFIED] | `quad.py`, `quad_spec.md` 1 to 3 |
| 66 | Listing printer: line number (width 3), tag, opcode (width 8), `arg1` and `arg2` (width 9), `result`; widths grow for wider operands; `ENDFOR` prints only its variable; an optional title gives a 74-character `== Title ===...` header. It reproduces the sample of `quad_spec.md` 9.2 | [OUR EXTENSION] | `quad.py` |
| 67 | Code generation: a number or scalar is its own place; each binary operator emits one quadruple into a fresh temporary, operands translated left to right; unary minus is `(-, 0, place, T)`; a scalar assignment is the translation of its right side followed by `(:=, place, -, x)`; loops are `FOR v lo hi` ... `ENDFOR v`; declarations produce no code; no folding. Array references raise `CodegenError` with the message `array references are not yet implemented (Phase 5): A[...]` and the line | [SIMPLIFIED] / [OUR EXTENSION] | `codegen.py`, roadmap 4.3 to 4.7 |
| 68 | `well_formedness_errors(quads)` implements the checks of `quad_spec.md` 3.2 that exist so far (matched and properly nested `FOR`/`ENDFOR`, `T` assigned once and before use, `P` assigned before use, loop variables read only inside their loop and never assigned, operand kinds) and returns a list of problems | [OUR EXTENSION] | roadmap Phase 4 structural checks |
| 69 | Phase 4 golden listings (`tests/golden/*.ml` and `*.quads`) are derived by hand from the specification, not generated by the code; the tests also use a test-local evaluator of the AST and of quadruple lists (independent of the Phase 6 interpreter) to check that generated code computes the same scalars | [OUR EXTENSION] | `tests/test_codegen_scalar.py` |

### Ambiguities in the roadmap or paper, and how Phase 1 treats them

| Ambiguity | Treatment |
|---|---|
| Paper prints `di = ui - li - 1` (Section 2) and `+ 1` (Section 3.1) | Use `+ 1` (decision 8, unchanged). |
| Paper indexes the boundary products `Δ` in reverse (`a[in, ..., i1]`) | Ordinary notation with `stridek`; same quantity (decision 9, unchanged). |
| Roadmap L0 order in the Phase 5 figure differs from its Section 5.5 T04 prediction (same counts) | Decision 35 fixes the Phase 5 order. |
| Roadmap writes `Z[i,j] += ...` in T11 but the language has no `+=` | `matrix_multiply.ml` writes the update out in full and clears `Z[i,j]` first. |
| Roadmap's T11 requires "known values in X and Y" but Phase 6.1 says memory has an address pattern | `Z` is cleared by the program; matrix contents are written by tests in a later phase. |
| The roadmap's lower-case `p1, t1` in the L3 figure vs upper-case `T1` in the listings | `T` for temporaries, `P` for address temporaries (decision 27). |
| Roadmap says "lo, hi integer literals" but Phase 3 mentions "negative literals" | Signed integer constants are accepted in bounds, `BASE` and loop bounds (`signed_int`). |
| Whether the paper's "at least n multiplications" matches the nested form's `n - 1` | Not argued; counters measure our own code (unchanged from roadmap Section 1.5). |

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
