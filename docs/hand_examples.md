# Hand-worked examples

Phase 1. All values here are derived by hand from the formulas of `quad_spec.md`, Section 5 (paper Eq. 1-5). They are **worked examples, not experimental results**. Each was also re-checked by a throwaway arithmetic script (outside the project, not part of the compiler): for every element of every array below, Eq. 1 and `CONSPART + VARPART` give the same address.

## 1. Method

For `A[l1:u1, ..., ln:un]` at `a`:

1. Sizes: `dk = uk - lk + 1`.
2. Strides: `stridek = d(k+1)*...*dn`, `striden = 1`; `size = d1*...*dn`.
3. **Direct (Eq. 1):** `D = a + SUM (ik - lk) * stridek`.
4. **Split (Eq. 2):** `C = (...((l1*d2 + l2)*d3 + l3)...)*dn + ln`; `CONSPART = a - C`; `VARPART = (...((i1*d2 + i2)*d3 + i3)...)*dn + in`; `D = CONSPART + VARPART`.
5. The two results must be equal.

## 2. The three array examples

### 2.1 `A[1:10, 1:20]` at 1000, element `A[2,3]`

| Item | Value |
|---|---|
| Dimensions | `n = 2`; `l = (1, 1)`, `u = (10, 20)` |
| Sizes | `d1 = 10 - 1 + 1 = 10`, `d2 = 20 - 1 + 1 = 20`; strides `(20, 1)`; size 200 |
| Direct (Eq. 1) | `1000 + (2-1)*20 + (3-1) = 1000 + 20 + 2 = 1022` |
| `C` | `1*20 + 1 = 21` |
| `CONSPART` | `a - C = 1000 - 21 = 979` |
| `VARPART` | `2*20 + 3 = 43` |
| Final address | `979 + 43 = 1022` |

Both methods give **1022**. ✓

### 2.2 `V[5:14]` at 500, element `V[9]`

| Item | Value |
|---|---|
| Dimensions | `n = 1`; `l = 5`, `u = 14` |
| Sizes | `d1 = 14 - 5 + 1 = 10`; stride `(1)`; size 10 |
| Direct (Eq. 1) | `500 + (9 - 5) = 504` |
| `C` | `l1 = 5` (for `n = 1` the nested form is just `l1`) |
| `CONSPART` | `500 - 5 = 495` |
| `VARPART` | `9` (for `n = 1`, just the subscript; no arithmetic) |
| Final address | `495 + 9 = 504` |

Both methods give **504**. ✓

### 2.3 `N[-5:5, -3:3]` at 200, element `N[-5,-3]`

| Item | Value |
|---|---|
| Dimensions | `n = 2`; `l = (-5, -3)`, `u = (5, 3)` |
| Sizes | `d1 = 5 - (-5) + 1 = 11`, `d2 = 3 - (-3) + 1 = 7`; strides `(7, 1)`; size 77 |
| Direct (Eq. 1) | `200 + (-5 - (-5))*7 + (-3 - (-3)) = 200 + 0*7 + 0 = 200` |
| `C` | `(-5)*7 + (-3) = -35 - 3 = -38` |
| `CONSPART` | `a - C = 200 - (-38) = 238` |
| `VARPART` | `(-5)*7 + (-3) = -38` |
| Final address | `238 + (-38) = 200` |

Both methods give **200** (the first element, address `a`). ✓ Note that `C` and `VARPART` are negative here; this is correct and the arithmetic must use signed integers.

## 3. First- and last-element check

The first element has address `a`; the last has address `a + size - 1`.

| Array | First element | Address | Last element | Direct address | `a + size - 1` |
|---|---|---|---|---|---|
| `A[1:10,1:20]` at 1000 | `A[1,1]` | `1000 + 0 = 1000` | `A[10,20]` | `1000 + 9*20 + 19 = 1199` | `1000 + 199 = 1199` ✓ |
| `V[5:14]` at 500 | `V[5]` | `500` | `V[14]` | `500 + 9 = 509` | `500 + 9 = 509` ✓ |
| `N[-5:5,-3:3]` at 200 | `N[-5,-3]` | `200` | `N[5,3]` | `200 + 10*7 + 6 = 276` | `200 + 76 = 276` ✓ |

## 4. Linear-subscript example

Array `A[1:10, 1:20]` at 1000; loops `i = 1 to 4` (outer), `j = 2 to 20` (inner); access `A[2*i + 1, j - 1]`. (Example file: `examples/linear_subscripts.ml`; roadmap test T04.)

**Step 1: array data.** `d = (10, 20)`, strides `(20, 1)`, `C = 21`, `CONSPART = 979`.

**Step 2: linear forms (Eq. 4)** `ik = ρk0 + ρk1*i + ρk2*j`:

| Subscript | Expression | `ρk0` | `ρk1` (coefficient of `i`) | `ρk2` (coefficient of `j`) |
|---|---|---|---|---|
| 1 | `2*i + 1` | 1 | 2 | 0 |
| 2 | `j - 1` | -1 | 0 | 1 |

**Step 3: coefficients (Eq. 5)** `D0 = SUM ρk0*stridek`, `Ds = SUM ρks*stridek`:

- `D0 = ρ10*20 + ρ20*1 = 1*20 + (-1)*1 = 19`
- `Di = ρ11*20 + ρ21*1 = 2*20 + 0*1 = 40`
- `Dj = ρ12*20 + ρ22*1 = 0*20 + 1*1 = 1`
- `K = CONSPART + D0 = 979 + 19 = 998`

**Step 4: the address.** `D = K + Di*i + Dj*j = 998 + 40*i + j`.

Cross-check by expanding the split form by hand: `VARPART = (1 + 2i)*20 + (-1 + j) = 20 + 40i - 1 + j = 19 + 40i + j`, so `D = 979 + 19 + 40i + j = 998 + 40i + j`. ✓ (This is the roadmap's `979 + (1*20 - 1*1) + 40i + j`.)

**Step 5: verify the first accessed address.** The first iteration is `i = 1, j = 2`:

- Subscripts: `2*1 + 1 = 3` and `2 - 1 = 1`, i.e. `A[3,1]`.
- Eq. 1 directly: `1000 + (3-1)*20 + (1-1) = 1000 + 40 + 0 = 1040`.
- Formula: `998 + 40*1 + 2 = 1040`. ✓

**Step 6: last accessed address.** `i = 4, j = 20`: subscripts `(9, 19)`; Eq. 1: `1000 + 8*20 + 18 = 1178`; formula: `998 + 160 + 20 = 1178`. ✓ The access executes `4 * 19 = 76` times.

**Whole sequence.** For each `i` the 19 addresses are consecutive (`j` has coefficient 1):

| `i` | first address (`j = 2`) | last address (`j = 20`) |
|---|---|---|
| 1 | 1040 | 1058 |
| 2 | 1080 | 1098 |
| 3 | 1120 | 1138 |
| 4 | 1160 | 1178 |

All subscripts stay within bounds: `2i + 1` takes 3..9 (declared 1..10) and `j - 1` takes 1..19 (declared 1..20).

## 5. Expected address trace: `examples/1d_sum.ml`

`V[5:14]` at 500; `C = 5`, `CONSPART = 495`; the access `V[i]` has `K = 495`, `D_i = 1`, so its address is `495 + i`.

The program has two loops; both use `i = 5..14`.

| Phase | Statement | Trace |
|---|---|---|
| Fill loop | `V[i] = 2 * i;` | `W 500, W 501, W 502, W 503, W 504, W 505, W 506, W 507, W 508, W 509` |
| Sum loop | `s = s + V[i];` | `R 500, R 501, R 502, R 503, R 504, R 505, R 506, R 507, R 508, R 509` |

Total 20 accesses: 10 writes then 10 reads. Element `V[i]` is at address `500 + (i - 5)`. Final memory: `V[i] = 2*i`, i.e. addresses 500..509 hold 10, 12, 14, ..., 28. Final scalar: `s = 2*(5 + 6 + ... + 14) = 2 * 95 = 190`. The result does not depend on the initial memory pattern because the fill loop overwrites all ten cells first.

## 6. Expected address trace: `examples/2d_traversal.ml`

`A[1:10,1:20]` at 1000; `K = 979`, `D_i = 20`, `D_j = 1`: the address of `A[i,j]` is `979 + 20*i + j`.

The loops visit `(i, j)` in row-major order; `j` varies fastest. The trace is 200 reads, in this order:

| `i` | `j = 1 .. 20` | addresses read |
|---|---|---|
| 1 | `A[1,1] .. A[1,20]` | `R 1000, R 1001, ..., R 1019` |
| 2 | `A[2,1] .. A[2,20]` | `R 1020, R 1021, ..., R 1039` |
| 3 | `A[3,1] .. A[3,20]` | `R 1040, ..., R 1059` |
| 4 | | `R 1060, ..., R 1079` |
| 5 | | `R 1080, ..., R 1099` |
| 6 | | `R 1100, ..., R 1119` |
| 7 | | `R 1120, ..., R 1139` |
| 8 | | `R 1140, ..., R 1159` |
| 9 | | `R 1160, ..., R 1179` |
| 10 | `A[10,1] .. A[10,20]` | `R 1180, R 1181, ..., R 1199` |

In short: the trace is exactly `R 1000, R 1001, R 1002, ..., R 1199`, in ascending order, with no gaps or repeats. This is the "visits 1000, 1001, ..., 1199 in order" check of roadmap Phase 6, and it holds for L0, L1, L2 and L3. The row start is `1000 + 20*(i - 1)`.

The final value of `s` is the sum of the initial memory contents of addresses 1000..1199; with the default pattern of `quad_spec.md`, Section 4.4 (`init(x) = (31*x + 7) mod 1009`) it is `s = 101387`. (A throwaway arithmetic check; no program of ours produced it.)

## 7. Other example programs: expected values

These follow from the same formulas and agree with the roadmap's Section 5.3.

| File | Array(s) | `K` and `D` | Expected trace |
|---|---|---|---|
| `3d_traversal.ml` | `M[0:3,0:4,0:5]` at 0: `d = (4,5,6)`, strides `(30,6,1)`, size 120, `C = 0`, `CONSPART = 0` | `K = 0`, `D = (30, 6, 1)` | reads `0, 1, 2, ..., 119` |
| `linear_subscripts.ml` | `A[1:10,1:20]` at 1000 | `K = 998`, `D = (40, 1)` | 76 reads; first 1040, last 1178 (Section 4) |
| `matrix_multiply.ml` | `X`, `Y`, `Z` each `[1:4,1:4]`, at 0, 16, 32: `d = (4,4)`, strides `(4,1)`, `C = 1*4 + 1 = 5` | `X`: `K = 0 - 5 = -5`, `D = (4, 0, 1)` (loops `i, j, k`); `Y`: `K = 16 - 5 = 11`, `D = (0, 1, 4)`; `Z`: `K = 32 - 5 = 27`, `D = (4, 1, 0)` | per `(i, j, k)`: read `Z[i,j]`, read `X[i,k]`, read `Y[k,j]`, write `Z[i,j]`; each `(i,j)` also begins with `Z[i,j] = 0` (one write) |

Checks for `matrix_multiply.ml`: `X[i,k]` is at `-5 + 4*i + k` (so `X[1,1] = 0`, `X[4,4] = 15`); `Y[k,j]` at `11 + 4*k + j` (`Y[1,1] = 16`, `Y[4,4] = 31`); `Z[i,j]` at `27 + 4*i + j` (`Z[1,1] = 32`, `Z[4,4] = 47`). The `Z` plan depends on `i, j` only, so at later levels it is hoisted out of the `k` loop. The roadmap's expected `Z = X*Y` requires known contents in `X` and `Y`; the program does not initialise them, so tests must write them first (a later phase).

Dynamic count for `matrix_multiply.ml`'s innermost statement: `4*4*4 = 64` executions of the update, each with 3 reads and 1 write, plus 16 clearing writes: `64*4 + 16 = 272` indexed accesses in total. (`3d_traversal.ml`: 120 reads. `1d_sum.ml`: 20 accesses. `2d_traversal.ml`: 200.)

## 8. Summary table of checks done

| Check | Result |
|---|---|
| Three roadmap examples: Eq. 1 = `CONSPART + VARPART` | 1022, 504, 200 ✓ |
| First element `= a`, last element `= a + size - 1` | ✓ (Section 3) |
| Linear example: formula `998 + 40i + j` = Eq. 1 at `i = 1, j = 2` | 1040 ✓ |
| Linear example: last address | 1178 ✓ |
| Identity for every element of every array used in the five example programs, the three hand examples and the roadmap's Phase 2 table (`V[0:9]`, `V[5:14]`, `A`, `M`, `N`), plus `S`, `U`, `H` | ✓ |
