# Notation cheat-sheet

Source: Guan, Yun, Sun, Su (2025), *Peephole Optimization Strategies for Intermediate Code in the Compilation Process*, and the project roadmap (Section 1.5 and Appendix A).

We use ordinary row-major notation `A[i1, ..., in]`. The paper writes the element as `a[in, ..., i1]` and labels the boundary products in reverse; the stride notation below is the same quantity (see "Stride and Delta").

Tags: **[PAPER]** stated in the paper, **[OURS]** a decision or notation we chose.

## Array declaration `A[l1:u1, ..., ln:un]`

| Symbol | Meaning |
|---|---|
| `n` | Number of dimensions |
| `li`, `ui` | Lower and upper bound of dimension `i` (integer literals, may be negative) |
| `a` | Address of the first element `A[l1, ..., ln]` |
| `es` | Loop control variable of loop `s` (`s = 1` is the outermost) |

## `di` - number of values of dimension i

    di = ui - li + 1                                   [OURS: +1]

The paper prints `di = ui - li - 1` in Section 2 (a typo) and `di = ui - li + 1` in Section 3.1. The count of values in a range is `ui - li + 1`, so we use **+1**. Example: `A[1:10, 1:20]` gives `d = (10, 20)`.

## Row-major address (Eq. 1) [PAPER]

    D = a + (i1 - l1)·d2·d3·...·dn + ... + (i(n-1) - l(n-1))·dn + (in - ln)

One storage unit per element. This is the baseline (L0) and the independent test oracle.

## `C` - constant of the information vector (Eq. 2) [PAPER]

    C = (...((l1·d2 + l2)·d3 + l3)...+ l(n-1))·dn + ln

All lower-bound terms of Eq. 1 folded together. Example: `A[1:10, 1:20]` gives `C = 1·20 + 1 = 21`.

## `CONSPART` and `VARPART` (Eq. 2) [PAPER]

    D = CONSPART + VARPART
    CONSPART = a - C
    VARPART  = (...((i1·d2 + i2)·d3 + i3)...+ i(n-1))·dn + in

- `CONSPART` is a compile-time constant: the address of the notional element `A[0, ..., 0]`. Computed once at declaration.
- `VARPART` depends on the subscript values and is the only part that needs run-time code.
- The paper's indexed quadruples: fetch `(=[ ], T1[T], -, x)` and store `([ ]=, x, -, T1[T])`, where `T1` holds CONSPART and `T` holds VARPART.

Example: `A[1:10, 1:20]` at `a = 1000`: `C = 21`, `CONSPART = 979`. For `A[3, 4]`: `VARPART = 3·20 + 4 = 64`, so `D = 979 + 64 = 1043`.

## `ρks` (rho) - linear subscript coefficients (Eq. 4) [PAPER]

    ik = ρk0 + Σ(s = 1..t) ρks·es

Subscript `k` is a linear function of the loop control variables `e1, ..., et`; the `ρ` values are integer constants. `ρk0` is the constant term; `ρks` is the coefficient of loop variable `es` in subscript `k`. Example: in `A[2*i + 1, j - 1]`, `ρ10 = 1, ρ11 = 2, ρ12 = 0` and `ρ20 = -1, ρ21 = 0, ρ22 = 1`.

## Stride and `Δ` (Eq. 3) [PAPER] / [OURS: notation]

The paper's boundary products `Δ` multiply each subscript's contribution to the address; they are indexed in reverse because the paper orders the element as `a[in, ..., i1]`. We define, in ordinary notation:

    stride_k = d(k+1)·d(k+2)·...·dn        (stride_n = 1)

This is the same quantity as the paper's `Δ`; only the labelling differs.

## `D0` and `Ds` - optimized coefficients (Eq. 5) [PAPER]

    <a[in, ..., i1]> = <a[0, ..., 0]> + D0 + Σ(s = 1..t) Ds·es
    Ds = Σk ρks·Δ(k-1)   ==  Σk ρks·stride_k   (our notation)
    D0 = Σk ρk0·stride_k

- `Ds` is the change of the address when loop variable `es` increases by 1.
- Because the `Δ` and `ρ` are constants, every `Ds` and `D0` is computed at compile time.
- `<a[0, ..., 0]>` is CONSPART.

## `K` [OURS: name from the roadmap]

    K = CONSPART + D0

The complete constant of an optimizable address, so that `Address = K + Σ Ds·es` (the L2 form).

## Quick self-test

- What is CONSPART? `a - C`, the address of `A[0, ..., 0]`, known at compile time.
- What is C? The lower bounds folded through the nested multiply-add form.
- Why `di + 1`? A dimension `l:u` has `u - l + 1` values; the paper's `-1` is a typo.
- What is `Ds`? The address change per unit step of loop variable `es`.
