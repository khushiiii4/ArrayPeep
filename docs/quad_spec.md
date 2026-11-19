# Quadruple, memory and address specification

Phase 1 specification. No code generator or interpreter exists yet; this document defines what they must produce and do.

Provenance tags: **[PAPER]** from the research paper, **[SIMPLIFIED]** a simplification for this student project, **[OUR EXTENSION]** our own design choice where the roadmap or paper is silent.

Related documents: `language_spec.md`, `hand_examples.md`, `notation_cheatsheet.md`, `design_decisions.md`.

Contents: 1 Quadruple format · 2 Operands · 3 Opcode reference · 4 Memory model and run-time check · 5 Row-major address calculation · 6 Tags and access windows · 7 Code levels L0-L3 · 8 Counting rules and metrics · 9 Tool output layouts · 10 Run-time errors.

## 1. Quadruple format

An instruction is a **quadruple** `(op, arg1, arg2, result)` [PAPER form]: an operation code and three fields. A field that an opcode does not use contains `-`. A listing is a numbered list of quadruples (numbers start at 1) executed in order, except for the loop markers of Section 3.

Every quadruple also carries a **tag**, `ADDR` or `BODY` (Section 6). The tag is not part of the four fields; it is metadata used for counting. [SIMPLIFIED]

Loops are **structured**: they are written with `FOR` and `ENDFOR` marker quadruples, not with labels and jumps, because the optimiser needs a clear "just before the loop" position. [SIMPLIFIED]

## 2. Operands

Every operand is one of:

| Kind | Written | Meaning |
|---|---|---|
| Integer literal | `20`, `-6`, `0` | A constant. May be negative. |
| Scalar | `s`, `x` | A named scalar cell (initially 0). |
| Loop variable | `i`, `j` | The current value of a loop counter; defined only between its `FOR` and `ENDFOR`. |
| Temporary | `T1`, `T2`, ... | Compiler-generated value holder (the paper's "new temporary"). See below. |
| Address temporary | `P1`, `P2`, ... | Compiler-generated holder of a running address, used by level L3 only. See below. |
| Indexed operand | `979[T2]`, `0[P2]`, `1043[0]` | `BASE[X]`: a memory cell, see below. Allowed only in the indexed opcodes. |

**`value(x)`** means: for a literal, the number itself; for a scalar, loop variable, `T` or `P`, its current value.

**Temporaries (`T<n>`)**

- Numbered `T1, T2, ...` in order of creation; the counter is global to a compilation and never reuses a number.
- **Assigned exactly once** in the listing (static single assignment), and that assignment precedes every use in listing order. (A temporary inside a loop body is of course assigned again at run time on every iteration; the rule is about the text of the listing.)
- Never a source-language name (`language_spec.md`, rule S12).

**Address temporaries (`P<n>`)** [OUR EXTENSION]

- Created only by the level-L3 optimisation (Section 7). They hold an address that is initialised before a loop and updated in place (`+ P2 1 P2`), so, unlike `T`, they may be assigned many times. The first assignment precedes every use in listing order.
- The roadmap's illustrative L3 listing writes them in lower case (`p1`, `p2`, `t1`); this specification writes them `P1`, `P2`, `T1`.

**Indexed operand `BASE[X]`** [PAPER: `T1[T]`, with the constant part as the base]

- `BASE` is an integer literal (may be negative). `X` is a literal, scalar, loop variable, `T` or `P`.
- **Effective address = `BASE + value(X)`.**
- At level L0, `BASE = 0` and `X` holds the full address, written `0[T5]`. At levels L1-L3, `BASE` is `CONSPART` (L1) or `K` (L2, L3) and `X` holds the variable part. The paper keeps CONSPART in a temporary `T1`; we write it directly as the base. [SIMPLIFIED]
- **Constant address:** when the address is a compile-time constant, `X` is the literal `0`: `1043[0]` accesses address 1043.

## 3. Opcode reference

There are exactly **eight** opcodes. No other opcode exists. In the table, "result" is the field named `result`; `-` marks an unused field.

| Opcode | arg1 | arg2 | result | Exact behaviour |
|---|---|---|---|---|
| `+` | value | value | `T` or `P` | `result := value(arg1) + value(arg2)` |
| `-` | value | value | `T` or `P` | `result := value(arg1) - value(arg2)` |
| `*` | value | value | `T` or `P` | `result := value(arg1) * value(arg2)` |
| `:=` | value | `-` | scalar or `P` | `result := value(arg1)` (copy) |
| `=[ ]` | indexed operand | `-` | `T` | **Indexed read.** `e = BASE + value(X)`; run-time check (Section 4.3); `result := memory[e]`; append `(R, e)` to the address trace. [PAPER: `(=[ ], T1[T], -, x)`] |
| `[ ]=` | value | `-` | indexed operand | **Indexed write.** `e = BASE + value(X)`; run-time check; `memory[e] := value(arg1)`; append `(W, e)` to the address trace. [PAPER: `([ ]=, x, -, T1[T])`] |
| `FOR` | loop variable `v` | literal `lo` | literal `hi` | **Loop entry**, see below. Printed `FOR v lo hi`. |
| `ENDFOR` | loop variable `v` | `-` | `-` | **Loop back-edge**, see below. Printed `ENDFOR v`. |

where "value" means a literal, scalar, loop variable, `T` or `P`.

Details:

- **Arithmetic** uses unbounded integers; there is no overflow. A scalar that has never been assigned has value 0.
- **Unary minus** has no opcode of its own. `-E` is translated as `(-, 0, place(E), T)`, i.e. a subtraction from the literal 0, and is counted as a subtraction. [OUR EXTENSION: the roadmap lists no unary opcode.] No constant folding is performed (roadmap Phase 4.7): `-5` in an expression produces `(-, 0, 5, T)`.
- **Copy `:=`** is used for scalar assignment (`s = T2` is `(:=, T2, -, s)`), for `s = 0;` (`(:=, 0, -, s)`), and at level L3 to initialise a `P` temporary (`(:=, 999, -, P1)`). A scalar assignment `s = s + i * 2;` always goes through a temporary and a copy, with no peephole shortcut.
- **Field layout of `FOR`/`ENDFOR`.** The roadmap prints `FOR i 1 3` and `ENDFOR i`; those are markers, so the fields are fixed by this table: `arg1` is the loop variable, `arg2` the lower bound, `result` the upper bound. [Roadmap Section 3.1 gives the form `FOR v lo hi` but not the field roles; this is our reading of it.]

### 3.1 Loop semantics (exact)

Let a `FOR v lo hi` quadruple be at position `f` and its matching `ENDFOR v` at position `e`. Matching is by nesting: each `FOR` matches the first following `ENDFOR` that closes it. A well-formed listing has them properly nested, with the same variable on both, and `lo`, `hi` literals.

**Executing `FOR v lo hi` (counted once per entry of the loop):**

1. If `lo <= hi`: set `v := lo` and continue with position `f + 1`.
2. If `lo > hi` (**zero-trip loop**): continue with position `e + 1`. The body and the `ENDFOR` are **not** executed and not counted; `v` is not assigned.

**Executing `ENDFOR v` (counted once per iteration, i.e. once after every execution of the body):**

1. Let `hi` be the upper bound of the matching `FOR`.
2. If `value(v) < hi`: set `v := v + 1` and continue with position `f + 1` (the first quadruple of the body).
3. Otherwise: continue with position `e + 1`; `v` keeps the value `hi`.

Consequences:

- The body runs `N = max(0, hi - lo + 1)` times; `ENDFOR` is executed `N` times; `FOR` once.
- `lo = hi` gives exactly one iteration. An **empty body** (`FOR` immediately followed by `ENDFOR`) is legal.
- The increment of `v` and the comparison are part of the `ENDFOR` quadruple and are **not** counted as extra additions or comparisons.
- Nested loops: the inner `FOR` executes once per iteration of the enclosing loop, so its entry is counted each time.
- Source programs cannot read `v` after its loop (`language_spec.md`, S11), so the value left in `v` is never observable.
- **Pre-header code.** Quadruples placed immediately before a `FOR` (level L3) execute once per entry of the loop, even if the loop is zero-trip. They must therefore be harmless: pure arithmetic on scalars/`P`, no memory access.
- **Termination.** Every loop has constant bounds and step 1, so every well-formed program terminates. The interpreter still stops with a run-time error after a step limit (Section 10) as protection against faulty generated code.

### 3.2 Well-formedness of a listing

A listing is well formed if: every `FOR` has a matching `ENDFOR` with the same variable and proper nesting; a loop variable is not the variable of an enclosing loop; every `T` is assigned exactly once and before every use; every `P` is assigned before its first use; every operand position holds a permitted kind (the table in Section 3); every indexed operand has a literal `BASE`. The code generator and optimiser must produce only well-formed listings.

## 4. Memory model and run-time check

### 4.1 Flat memory [SIMPLIFIED]

There is one flat integer memory. Conceptually it has a cell for every integer address, but **only cells inside declared array regions (Section 4.2) exist for the program**: any other address is invalid (Section 4.3). **One cell holds one array element** (the paper's "one storage unit per element"). Scalars live in **separate named cells** outside array memory; they never alias array elements.

### 4.2 Array regions

An array with base `a` and `size = d1 * d2 * ... * dn` elements occupies the **region** `a ... a + size - 1`. Its first element `A[l1, ..., ln]` is at `a`; its last element `A[u1, ..., un]` is at `a + size - 1`. Regions of different arrays must not overlap (`language_spec.md`, rule S7). Addresses may be negative.

### 4.3 Effective address and the run-time check

The effective address of an indexed access is `BASE + value(X)`. **An indexed access whose effective address lies outside every declared array region is a run-time error.** [Roadmap Phase 1.3] Execution stops and reports the message of Section 10.

Limitations, documented rather than hidden:

- The check is on the **address**, not on each subscript. A subscript that is out of range for its own dimension but still produces an address inside the same array or inside another array is **not** detected. Example: for `A[1:10, 1:20]`, `A[1, 25]` has the address of `A[2, 5]`. Levels L2/L3 never form the subscripts at all, so they could not check them either. This follows the roadmap's definition of the check.
- An access that lands in a different declared array than intended is likewise accepted. The equivalence check across levels (same address trace) is what exposes such a mistake.

### 4.4 Initial contents [OUR EXTENSION]

The roadmap asks for a fixed address-dependent initial pattern (Phase 6.1) but does not fix it. We fix it here so that results are reproducible and wrong-address reads are visible: before the program runs, the cell at address `x` inside any declared array holds

    init(x) = (31 * x + 7) mod 1009      (mathematical modulo: result in 0..1008, also for negative x)

All levels start from the same contents. Programs should not depend on the pattern except in tests that say so. Examples: `init(1000) = 737`, `init(1001) = 768`, `init(0) = 7`, `init(-1) = 985`. (Checked by arithmetic during Phase 1.) Phase 6 may replace the pattern only by recording a new design decision.

**Period of the pattern.** Because the modulus is 1009 (a prime) and 31 is invertible modulo 1009, `init` is injective on any 1009 consecutive addresses, so a read of a wrong address *within* such a span returns a different value. Addresses 1009 or more apart can hold the same initial value, so for arrays with more than 1009 cells (for example the larger Phase 12 scaling runs) a wrong-address read could by chance return the same value. Value comparison alone is then weaker; **the address-trace comparison is unaffected** and remains the primary equivalence check (Section 4.6). Matrix tests that need specific matrix values write them explicitly first.

### 4.5 Scalars

Scalars are created on first mention with value 0 and hold unbounded integers (`language_spec.md`, Section 4.2).

### 4.6 What is observable

The behaviour of a run is the triple **(final scalar values, final memory image, ordered address trace)**. The **memory image** is the final contents of the cells of the **declared array regions only** (a mapping from address to value over exactly the addresses of Section 4.2); no other address exists. The **final scalar values** are the values of all scalars that were mentioned in the program (never-assigned scalars are 0). The trace is the ordered list of `(R|W, address)` produced by every executed `=[ ]` (R) and `[ ]=` (W). Two levels are equivalent iff all three are equal (roadmap Section 5.4: V.1 and V.2).

## 5. Row-major address calculation [PAPER]

### 5.1 Definitions

For an array `A[l1:u1, ..., ln:un]` with base address `a`:

| Symbol | Name | Definition |
|---|---|---|
| `n` | number of dimensions | 1 to 4 |
| `lk`, `uk` | lower / upper bound of dimension `k` | integer constants, `uk >= lk` |
| `dk` | size of dimension `k` | `uk - lk + 1` |
| `a` | base address | address of `A[l1, ..., ln]` |
| `stridek` | stride of dimension `k` | `d(k+1) * d(k+2) * ... * dn`, and `striden = 1` |
| `size` | number of elements | `d1 * d2 * ... * dn` |
| `C` | constant of the information vector | `(...((l1*d2 + l2)*d3 + l3)...)*dn + ln` |
| `CONSPART` | constant part of an address | `a - C` |
| `VARPART` | variable part of an address | `(...((i1*d2 + i2)*d3 + i3)...)*dn + in` |

`dk = uk - lk + 1`. The paper prints `-1` in Section 2 and `+1` in Section 3.1; `+1` is correct because a range `l..u` contains `u - l + 1` values. This ambiguity is documented in `notation_cheatsheet.md` and `design_decisions.md`.

`stridek` is our notation for the quantity the paper calls the boundary product `Δ`; the paper indexes `Δ` in the reverse direction (it writes the element as `a[in, ..., i1]`). Subscripts are written here in ordinary order `A[i1, ..., in]`. Details in `notation_cheatsheet.md`.

The array information vector (the paper's Table 1) stores `lk`, `uk`, `dk`, `n`, the element type, `C` and `a`. For static arrays it lives only in the compiler's symbol table.

### 5.2 The two formulas

**Direct form (Eq. 1)** [PAPER]:

    D = a + (i1 - l1)*d2*d3*...*dn + (i2 - l2)*d3*...*dn + ... + (i(n-1) - l(n-1))*dn + (in - ln)
      = a + SUM over k of (ik - lk) * stridek

**Split form (Eq. 2)** [PAPER]:

    D = CONSPART + VARPART        CONSPART = a - C

### 5.3 Why they are equal

The nested ("Horner") form of `VARPART` expands to `SUM ik * stridek`, and the nested form of `C` expands to `SUM lk * stridek`. Therefore

    CONSPART + VARPART = (a - SUM lk*stridek) + SUM ik*stridek = a + SUM (ik - lk)*stridek = D.

`CONSPART` depends only on declared constants, so it is computed **once at compile time**. Only `VARPART` depends on the subscript values and needs run-time code. For `n = 1`, `VARPART = i1` (no arithmetic). For `n = 2`, evaluating `VARPART = i1*d2 + i2` takes one multiplication and one addition (roadmap Phase 5.2 check).

The paper states that a direct evaluation needs "at least `n` multiplications"; the nested form needs `n - 1` multiplications plus whatever the subscript expressions themselves need. We do not argue with the paper; our counters measure what our own generated code does (roadmap Section 1.5).

### 5.4 Worked check

`A[1:10, 1:20]` at 1000, element `A[2,3]`: `d = (10, 20)`, `C = 1*20 + 1 = 21`, `CONSPART = 979`, `VARPART = 2*20 + 3 = 43`, `979 + 43 = 1022`; direct: `1000 + (2-1)*20 + (3-1) = 1022`. More examples (and the first/last-element check) are in `hand_examples.md`.

### 5.5 Linear subscripts and the optimised address (used by L2 and L3) [PAPER]

If every subscript is linear in the loop control variables `e1, ..., et` (Eq. 4):

    ik = ρk0 + SUM over s of ρks * es        (ρ = integer constants)

then (Eq. 5) the address is

    D = CONSPART + D0 + SUM over s of Ds * es,   D0 = SUM over k of ρk0 * stridek,   Ds = SUM over k of ρks * stridek.

Every `D0` and `Ds` is a compile-time constant. We name `K = CONSPART + D0`, so `D = K + SUM Ds*es`. The derivation is done in `hand_examples.md`, Section 4 (and later in `derivation.md`, Phase 8).

## 6. Tags and access windows

**Tags** [SIMPLIFIED]:

- **`ADDR`**: a quadruple created while computing the address of an array element: the code that evaluates the subscripts, the lower-bound subtractions (L0), the `VARPART` multiply-adds, the base addition (L0), and the `P`-initialisations and increments of L3.
- **`BODY`**: everything else, including the **indexed access quadruple itself** (`=[ ]`, `[ ]=`), the arithmetic of the user's expressions, `:=` for user assignments, `FOR` and `ENDFOR`.

[OUR EXTENSION on the roadmap's wording] The roadmap says ADDR is "created while computing an array address". We read this as excluding the final memory access, so that "dynamic ADDR" counts address arithmetic only and loads/stores are counted separately.

**Access window** (roadmap Phase 5.5, 7.5): for every array access the code generator records the contiguous run of quadruples that computes its subscripts and `VARPART` (or the full address at L0), ending just before the access quadruple. The window together with the access quadruple is the "peephole" the optimiser later replaces. The window is a **contiguous** run that contains **all** the `ADDR` quadruples of that access, and nothing else except, when a subscript itself contains an array element (`V[W[i]]`), the nested access: its own `ADDR` quadruples and its `=[ ]` (tagged `BODY`) lie inside the outer window. Such an access is not linear and is not rewritten, so the window of an **optimisable** access never contains a nested access and then consists of exactly the `ADDR` quadruples of that access. (The roadmap's Phase 7 window test "covers exactly the ADDR-tagged quadruples" therefore applies to optimisable accesses.)

## 7. Code levels L0-L3

The program can be generated and run in four versions, to show the effect of each idea in the paper separately. The choice of baseline strongly affects any reported improvement, so **the definition of L0 must appear in the final report**. [Roadmap Section 3.1]

| Level | Name | Origin |
|---|---|---|
| L0 | Naive Eq. 1 | [SIMPLIFIED] our unoptimised baseline |
| L1 | CONSPART + VARPART | [PAPER] Eq. 2, Section 3 |
| L2 | Linear-form address | [PAPER] Eq. 4-5 |
| L3 | Hoisted + incremental | [OUR EXTENSION] from the paper's Section 1 principles |

What is the paper's and what is ours:

- The paper gives the mathematics (Eq. 1-5), the array information vector, the indexed access quadruples, the syntax-directed translation of subscript lists, and the four guiding principles. **It gives no pseudo-code for the optimisation algorithm** and no L0 baseline.
- L0 is a definition we chose so there is an unoptimised baseline to compare against.
- L1 is the paper's `CONSPART + VARPART` code.
- L2 is the direct use of Eq. 4-5; the exact emission rules (term order, when a multiplication is skipped) are ours.
- L3 (hoisting and incremental update) realises the paper's principles "computation that can be done in an outer loop should be moved up" and "regularly changing addresses should use addressing operations", but the scheme itself is **our own design**.

The paper reports that optimising index variables can sometimes double the speed of the target code, measured as compile time in QEMU 1.7.2. We do **not** reproduce those experiments or quote its numbers; we count executed quadruples in our own interpreter.

### 7.1 Running example

`A[1:10, 1:20]` at 1000: `d = (10, 20)`, strides `(20, 1)`, `C = 21`, `CONSPART = 979`. Statement `s = s + A[i, j];` inside `for i = 1 to 10` / `for j = 1 to 20`. Listings below show the address window plus the access; temporaries are numbered from `T1` here for readability. The tag column is shown.

**L0: naive Eq. 1** [SIMPLIFIED]. Nothing is precomputed: every lower bound is subtracted at run time, even a lower bound of 0, and the base address is added at run time. The access goes through `0[T]`.

Emission rule for `A[E1, ..., En]` (our canonical order, which reproduces the roadmap's Phase 5 figure). `U1`, `U2`, `V`, `W` below are only **names for the generated temporaries `T<n>`** in this description (they are not listing names and must not be confused with the reserved address temporaries `P<n>`); each is a fresh `T`:
1. Emit the code of `E1`; then `U1 := E1 - l1`.
2. For `k = 2..n`: emit the code of `Ek`; then `V := U(k-1) * dk`; then `W := Ek - lk`; then `Uk := V + W`.
3. `T := Un + a`; access `0[T]`.

```
 1  ADDR  -     i      1      T1        # i - l1
 2  ADDR  *     T1     20     T2        # * d2
 3  ADDR  -     j      1      T3        # j - l2
 4  ADDR  +     T2     T3     T4
 5  ADDR  +     T4     1000   T5        # + a
 6  BODY  =[ ]  0[T5]  -      T6        # read
```

**L1: CONSPART + VARPART** [PAPER]. `CONSPART = 979` is folded at compile time and is the base of the access. Emission rule for `A[E1, ..., En]` (ordered, like L0; this is the paper's `PLACE`/`LIMIT` scheme, roadmap Phase 5.1): set `PLACE` to the place of `E1` (after emitting the code of `E1`); then for `k = 2..n`: **emit the code of `Ek` first**, then `T := PLACE * dk`, then `T' := T + Ek`, and set `PLACE := T'`. The access is `CONSPART[PLACE]`. For `n = 1` there is no arithmetic: the access is `CONSPART[E1]`. This is also the **fallback** level for non-linear subscripts.

```
 1  ADDR  *     i        20     T1
 2  ADDR  +     T1       j      T2
 3  BODY  =[ ]  979[T2]  -      T3        # read
```

**L2: linear-form address** [PAPER Eq. 4-5; emission rules OUR EXTENSION]. Every subscript is linear; the whole address is `K + SUM Ds*es` with `K` and each `Ds` computed at compile time. Emission rule (roadmap Phase 8.5, 8.6): terms with `Ds = 0` disappear; each remaining term is `es` itself when `Ds = 1`, otherwise `es * Ds` (one multiplication); terms are added left to right in loop order (outermost first); the access is `K[T]`. If no term remains the address is constant: `K[0]`. If one term with `Ds = 1` remains, `T` is the loop variable and **no arithmetic is emitted**. Here `K = 979`, `Di = 20`, `Dj = 1`, so L2 equals L1 for this simple access ("L1 and L2 are close for simple accesses", roadmap Section 3.1):

```
 1  ADDR  *     i        20     T1
 2  ADDR  +     T1       j      T2
 3  BODY  =[ ]  979[T2]  -      T3        # read
```

**L3: hoisted + incremental** [OUR EXTENSION]. Starting from L2: partial sums that depend only on outer loop variables are computed before the inner loop (in the loop "pre-header"), and the address is advanced by the constant `Ds` once per iteration instead of being recomputed (roadmap Phase 9). One `P` per level; initial value of `P1` is `K + Di*lo_i = 979 + 20 = 999`; `P2 := P1 + Dj*lo_j = P1 + 1`. This is the expected shape (roadmap Phase 9 figure), here with the full statement:

```
 1  ADDR  :=    999    -      P1        # K + Di*lo_i = 979 + 20*1
 2  BODY  FOR   i      1      10
 3  ADDR  +     P1     1      P2        # P1 + Dj*lo_j
 4  BODY  FOR   j      1      20
 5  BODY  =[ ]  0[P2]  -      T1        # access: no address arithmetic
 6  BODY  +     s      T1     T2
 7  BODY  :=    T2     -      s
 8  ADDR  +     P2     1      P2        # P2 += Dj
 9  BODY  ENDFOR j
10  ADDR  +     P1     20     P1        # P1 += Di
11  BODY  ENDFOR i
```

Check: at `i = 1, j = 1` the access address is `999 + 1 = 1000 = A[1,1]`; the trace is 1000, 1001, ..., 1019, then 1020 for `i = 2`. For a negative `Ds` the update subtracts (`- P1 20 P1` for `Ds = -20`). A profitability guard applies (roadmap 9.4): L3 is used for an access only if the arithmetic per innermost iteration strictly decreases compared with L2; otherwise the access stays at L2 and the reason is reported (for example a 1-D access `V[i]` needs no arithmetic at L2).

### 7.2 Second example: `A[2*i + 1, j - 1]`, `i = 1..4`, `j = 2..20` (test T04)

`K = 998`, `Di = 40`, `Dj = 1` (derived in `hand_examples.md`, Section 4). The access executes `4 * 19 = 76` times. Address windows, by the rules above, with the per-execution operation counts that match the roadmap's worked prediction (Section 5.5 of the roadmap):

| Level | Window | Per execution |
|---|---|---|
| L0 | `*  2 i T1` · `+ T1 1 T2` · `- T2 1 T3` · `- j 1 T4` · `* T3 20 T5` · `- T4 1 T6` · `+ T5 T6 T7` · `+ T7 1000 T8` · access `0[T8]` | 2 mult, 6 add/sub |
| L1 | `* 2 i T1` · `+ T1 1 T2` · `- j 1 T3` · `* T2 20 T4` · `+ T4 T3 T5` · access `979[T5]` | 2 mult, 3 add/sub |
| L2 | `* i 40 T1` · `+ T1 j T2` · access `998[T2]` | 1 mult, 1 add |
| L3 | pre-header `:= 1038 - P1` (before `FOR i`), `+ P1 2 P2` (before `FOR j`); access `0[P2]`; `+ P2 1 P2` (before `ENDFOR j`); `+ P1 40 P1` (before `ENDFOR i`) | 0 mult, 1 add (inner) |

Totals over the 76 executions (hand predictions for this emission scheme, **not measurements**): L0 152 mult / 456 add-sub; L1 152 / 228; L2 76 / 76; L3 0 mult and `76 + 4 + 4 = 84` additions plus one initialisation. L3 trace check: `P1` starts at `998 + 40 = 1038`, the first `P2` is `1038 + 2 = 1040` (`A[3,1]`), and for `i = 2` the first `P2` is `1078 + 2 = 1080`.

**Ambiguity noted.** The roadmap's Phase 5 figure orders the L0 operations as `i-1`, `*20`, `j-1`, `+`, `+1000`, while its Section 5.5 prediction lists the T04 operations as `2*i; +1; -1; j-1; -1; *20; add; +1000`. The two orders differ, but the operation counts are identical. This specification fixes the order of the L0 rule above (it reproduces the Phase 5 figure exactly); a counted-operation prediction is unaffected.

## 8. Counting rules and metrics

Static count: the number of quadruples in a listing (including `FOR` and `ENDFOR`). Dynamic count: the number of quadruples **executed**.

**What is counted** [roadmap Section 3.1]:

- **`FOR` is executed once per entry of the loop** (so once for the outermost loop, and once per iteration of its parent for a nested loop).
- **`ENDFOR` is executed once per iteration.**
- Every other quadruple counts once per execution.
- A zero-trip loop executes `FOR` once and nothing else of that loop.

Sanity formula (roadmap Phase 6): a single loop with `N >= 1` iterations whose body has `k` quadruples executes `1 + N*(k + 1)` quadruples.

Worked example: `for i = 1 to 3 do s = s + i * 2; end` translates to

```
1  BODY  FOR     i   1   3
2  BODY  *       i   2   T1
3  BODY  +       s   T1  T2
4  BODY  :=      T2  -   s
5  BODY  ENDFOR  i
```

Static count 5. Dynamic count `1 + 3*(3 + 1) = 13`: `FOR` 1, `*` 3, `+` 3, `:=` 3, `ENDFOR` 3. Multiplications executed 3; additions/subtractions 3.

**Metrics** [roadmap Section 5.5], collected per run:

| Metric | Definition |
|---|---|
| Static quadruples | Number of quadruples in the listing. |
| Dynamic quadruples | Quadruples executed, total and per opcode, split by tag (ADDR / BODY). |
| Dynamic ADDR quadruples | Executed quadruples tagged ADDR. |
| Multiplications executed | Executed `*` quadruples (total, and ADDR only). |
| Additions / subtractions executed | Executed `+` and `-` quadruples (total, and ADDR only). Unary minus counts here. |
| Indexed loads and stores | Executed `=[ ]` and `[ ]=`. **Must be identical across levels** (sanity check). |
| Weighted cost (optional) | `SUM weight * count` with stated weights (for example multiplication 3, others 1). Weights are an assumption and must be labelled as such. |

Per-opcode counts must add up to the total. Improvement ratios are computed in code from the counters. Every number in the final report must come from running our own program; numbers in the roadmap and in this specification are hand-derived worked examples, not experimental results.

**Correctness is measured by equivalence** (roadmap Section 5.4): for every program and every level, final scalars equal, memory image equal, and address trace equal to the baseline's and to the independent oracle's (an implementation of Eq. 1 in plain Python that imports nothing from the compiler). The trace is the strongest check because it compares every accessed address in order, not only the final results.

## 9. Tool output layouts

Layouts fixed in roadmap Section 5.2. The first blocks contain hand-derived values for test T04; the metrics block shows only the layout. Block headers have the form `== <title> ` followed by `=` characters up to a total width of 74.

### 9.1 Array information vector (one block per array, declaration order)

```
== Array information vector ==============================================
 array A   n = 2   type = int   a = 1000
  dim  l   u   d
   1   1   10  10     stride 20
   2   1   20  20     stride 1
 C = 21    CONSPART = a - C = 979    size = 200
```

### 9.2 Quadruple listing (baseline and optimised)

```
== Quadruples (L1) =======================================================
  1  BODY  FOR     i        1        10
  2  BODY  FOR     j        1        20
  3  ADDR  *       i        20       T1
  4  ADDR  +       T1       j        T2
  5  BODY  =[ ]    979[T2]  -        T3
  6  BODY  +       s        T3       T4
  7  BODY  :=      T4       -        s
  8  BODY  ENDFOR  j
  9  BODY  ENDFOR  i
```

Columns: line number (right-aligned, width 3), tag, opcode, `arg1`, `arg2`, `result`, separated by spaces, padded for readability. Unused fields print `-`; trailing unused fields of `ENDFOR` may be omitted. The title shows the level (`L0`..`L3`). [OUR EXTENSION: the roadmap says only that the listing numbers the lines and "your formatting may differ".]

### 9.3 Optimization report (one entry per array access, in source order)

```
== Optimization report ===================================================
 access A[2*i+1, j-1]  (read, line 4)
   linear forms : subscript 1 = 1 + 2*i ; subscript 2 = -1 + 1*j
   K = 998   D_i = 40   D_j = 1
   decision     : L3 applied (address arithmetic per inner iteration drops)
```

The `decision` line is one of:

| Decision text | Meaning |
|---|---|
| `L3 applied (...)` | Hoisting and incremental update used. |
| `kept at L2 (no arithmetic to save)` | Guard rejected L3, e.g. `V[i]` in a 1-D loop. |
| `L2 applied (constant address, no run-time arithmetic)` | All `Ds` are 0 (e.g. `A[3,4]` outside any loop). |
| `kept at L1 (not linear)` | Fallback; the `linear forms` line carries the reason. |

Non-linear reasons, exactly as in the roadmap's table (Phase 7): `NOT LINEAR: product of loop variables`, `NOT LINEAR: variable that is not a loop variable`, `NOT LINEAR: array element in subscript`. The `linear forms` line for a linear access lists each subscript as `rho_k0 + rho_k1*e1 + ...`; `K` and the `D` coefficients follow, one `D_<loopvar>` per enclosing loop (a `D` of 0 means the address does not depend on that loop).

### 9.4 Verification

```
== Verification ==========================================================
 L0 vs L1 : scalars equal, memory equal, address trace equal   PASS
 L0 vs L2 : scalars equal, memory equal, address trace equal   PASS
 L0 vs L3 : scalars equal, memory equal, address trace equal   PASS
 oracle   : address trace equal                                PASS
 verdict  : PASS
```

Each row ends in `PASS` or `FAIL`. A failing row replaces the descriptive text with the **first difference**: which comparison failed first and the differing item, for example `address trace differs at access 17: L0 (R, 1040) vs L2 (R, 1041)`. The final `verdict` line is `PASS` only if every row passed. [`verdict` line: OUR EXTENSION]

### 9.5 Metrics comparison table (layout; numbers come from measurements)

```
== Metrics ===============================================================
 level | static quads | dynamic quads | dynamic ADDR | multiplications | add/sub
 L0 ...
 L1 ...
 L2 ...
 L3 ...
```

The roadmap's header text "(layout only; fill from your run)" marks its own sample as a layout; it is **not** part of the tool's output. The real header is `== Metrics ===…` (74 characters wide, like the other headers); the `...` rows above stand for the measured numbers.

Additional counters (loads, stores, ADDR-only multiplications and additions, improvement ratios) may be shown in a second table; its layout is fixed when the command-line tool is integrated (Phase 10).

## 10. Run-time errors

A run-time error stops execution immediately, prints one message and ends the tool with exit code 2 (`language_spec.md`, Section 9). No result, counters or verification are produced for that run. [roadmap Phase 1.8, 6.4]

**Message format.** Run-time and internal messages are single lines on standard error. They start with `runtime error:` or `internal error:` and carry **no file name and no source line**, because they refer to a quadruple of the generated listing, not to a source position (`language_spec.md`, Section 9). `<n>` is the position in the listing of the level being executed, and the message states which level it was (`level L<k>`) when more than one level is run, for example `runtime error: level L0, quadruple 5 [...]: ...`. [OUR EXTENSION]

**Several levels in one run (`--level all`, `--compare`).** The baseline L0 is executed first.
- If **L0** raises a run-time error, the program itself is invalid: report the L0 error, exit code 2, and run no other level.
- If L0 completes but **a later level** raises a run-time error (or a different one), the optimised code is wrong: this is a **verification FAIL** (exit code 3). The verification block names the level and the first difference, for example `L2 : runtime error at quadruple 4 where L0 completed`.
- A step-limit error follows the same two rules.
- An **internal error** (ill-formed generated code) in any level ends the run with exit code 4.

| Error | Message layout | Example |
|---|---|---|
| Invalid memory access (Section 4.3) | `runtime error: quadruple <n> [<op> <arg1> <arg2> <result>]: effective address <e> is outside every declared array` (with a single level; with several levels `level L<k>, ` follows `runtime error: `) | `runtime error: quadruple 5 [=[ ] 979[T2] - T3]: effective address 1224 is outside every declared array` |
| Step limit exceeded | `runtime error: step limit of <N> executed quadruples exceeded` | default limit `N = 10,000,000`, adjustable in the tool [OUR EXTENSION] |
| Internal error (generated code not well formed, e.g. a `T` read before assignment) | `internal error: <description>` (exit code 4) | indicates a compiler bug, not a user error |

The quadruple number `<n>` is the 1-based position in the listing being executed (so the message names the exact quadruple, roadmap Phase 6.4).

Example of an invalid memory access: `array V[0:9] at 100;` and `s = V[12];`. The address is `100 + 12 = 112`, which lies outside `100..109` (and outside every other region), so the read stops with a run-time error. By contrast `s = V[10]` would have address 110 and also be an error, while for a 2-D array `A[1:10,1:20]` the access `A[1, 25]` (address `1000 + 24 = 1024`) is *inside* the region and is silently accepted (Section 4.3, first limitation).
