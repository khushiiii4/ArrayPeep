# Bug log

Phase 11 (correctness testing), 2026-11-20. Format: symptom, cause, fix, test added.

## Compiler defects found during Phase 11

**None.** No defect in the lexer, parser, semantic analysis, code generation, the L2/hoist/L3 optimizer, the interpreter, the driver or the command line was found during Phase 11 testing, and no production code was changed in Phase 11.

What was run to look for defects (details in `test_report.md`):

- T01-T18 of the roadmap (Section 5.3) at L0, L1, L2, hoist and L3: all agree with the independent oracle and with each other.
- The edge cases of task 11.4 and the robustness tests of task 11.5.
- 300 generated programs (seeds 101, 202, 303, 404, 505; 60 each): all levels agree with L0 and with the oracle.
- Eight deliberate faults in a temporary copy: every one was detected (so the tests are able to see address-calculation bugs; the absence of defects is not caused by blind tests).

Entries below are therefore not compiler bugs; none was invented.

## Defects in the new test code, found and fixed while writing it

These are mistakes in the Phase 11 tests themselves, caught by running them. They are listed so that the log is complete; the "test added" column is the corrected test.

| # | Symptom | Cause | Fix | Test |
|---|---|---|---|---|
| T-1 | `OracleError: V: subscript 1 = 1 is outside 5..14` in the first run of `test_oracle.py` | The test looped `i` over 1..2 but the array `V` is declared `V[5:14]`; the oracle correctly rejected it | Loop range changed to 5..6 | `TraceTests.test_same_element_twice_in_one_statement` |
| T-2 | `ModuleNotFoundError: No module named 'equivalence'` when a test module was run as `python -m unittest tests.test_equivalence` (it worked under `discover -s tests`) | The `tests` directory is only on `sys.path` under `discover`; helper modules are imported by plain name | Each test module that imports a helper puts its own directory on `sys.path` first | every Phase 11 test module; run once with `tests.<module>` and once with `discover` |
| T-3 | `AssertionError: Tuples differ ... <function linear.<locals>.evaluate ...>` in `test_programs_are_deterministic` | The test compared oracle descriptions, which contain closures; two equal descriptions never compare equal | Compare the source text, the arrays and the oracle trace instead | `GeneratorTests.test_programs_are_deterministic` |

## Behaviours checked and found to be as designed (not defects)

- S11 ("loop variable used outside its loop") is reported as S10 (`language_spec.md` Section 5, design decision 44); the sweep in `test_robustness.py` expects the S10 message.
- The roadmap's T14 row says "non-loop scalar"; the report text is the Phase 7 table text "variable that is not a loop variable".
- T13 produces three access entries: the `W[i]` write, the `V[W[i]]` read (fallback) and the `W[i]` read nested in its subscript (kept inside the fallback window).
