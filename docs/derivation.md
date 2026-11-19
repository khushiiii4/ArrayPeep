# Derivation of the optimised address (paper Eq. 5)

Phase 8, task 8.1. This is the hand derivation for a 2-D array; the code in `arraypeep/precompute.py` is its n-dimensional form. Notation follows `notation_cheatsheet.md`: row-major `A[i1, i2]`, `d2` the size of the second dimension, `stride1 = d2`, `stride2 = 1`.

## 1. Starting point: Eq. 1 and Eq. 2

For `A[l1:u1, l2:u2]` with first address `a`:

    D = a + (i1 - l1)*d2 + (i2 - l2)
      = (a - C) + (i1*d2 + i2)           with C = l1*d2 + l2
      = CONSPART + VARPART

`CONSPART = a - C` is fixed by the declaration. Only `VARPART = i1*d2 + i2 = i1*stride1 + i2*stride2` depends on the subscripts.

## 2. Linear subscripts: Eq. 4

Let `e1, ..., et` be the loop variables of the loops that enclose the access. Each subscript is linear in them:

    i1 = r10 + SUM_s r1s * es
    i2 = r20 + SUM_s r2s * es          (r = integer constants)

## 3. Substitution

    VARPART = i1*stride1 + i2*stride2
            = (r10 + SUM_s r1s*es)*stride1 + (r20 + SUM_s r2s*es)*stride2
            = (r10*stride1 + r20*stride2) + SUM_s (r1s*stride1 + r2s*stride2) * es
            = D0 + SUM_s Ds * es

with

    D0 = SUM_k rk0 * stride_k          Ds = SUM_k rks * stride_k

Every `rk0`, `rks` and `stride_k` is a compile-time constant, so `D0` and every `Ds` are too. A loop variable that appears in no subscript has `Ds = 0`.

## 4. The optimised address: Eq. 5

    D = CONSPART + D0 + SUM_s Ds * es = K + SUM_s Ds * es,        K = CONSPART + D0

`K` is the base of the indexed access `K[T]`; `T = SUM_s Ds * es` is all that is left to compute at run time. The L2 code builds `T` with one multiplication per term whose `Ds` is not 0 or 1 and one addition per further term; terms with `Ds = 0` vanish, and a single term with `Ds = 1` is the loop variable itself (no arithmetic).

## 5. Worked check (roadmap T04)

`A[1:10, 1:20]` at 1000, `A[2*i + 1, j - 1]`: strides `(20, 1)`, `CONSPART = 979`; `r10 = 1, r11 = 2`; `r20 = -1, r22 = 1`.

    D0 = 1*20 + (-1)*1 = 19        Di = 2*20 + 0*1 = 40        Dj = 0*20 + 1*1 = 1
    K  = 979 + 19 = 998            D  = 998 + 40*i + j

At `i = 1, j = 2` the subscripts are `(3, 1)`: Eq. 1 gives `1000 + 2*20 + 0 = 1040` and the formula `998 + 40 + 2 = 1040`.

## 6. Mapping to the code

| Symbol | Code |
|---|---|
| `rk0`, `rks` | `LinearForm.const`, `LinearForm.coeffs` (a missing loop variable means 0) |
| `stride_k`, `CONSPART` | `ArrayInfo.strides`, `ArrayInfo.conspart` |
| `D0`, `Ds`, `K` | `AccessPlan.d0`, `AccessPlan.ds`, `AccessPlan.k` (`make_plan`) |
| L2 code | `emit_l2`, `optimize_l2` in `optimizer.py` |
