"""Compile-time address plans (paper Eqs. 2, 3 and 5): K, D0 and one D_s per loop variable.

For an access ``A[i1, ..., in]`` whose subscripts are linear (Eq. 4),
``i_k = rho_k0 + SUM_s rho_ks * e_s``, the address is (Eq. 5)

    CONSPART + D0 + SUM_s D_s * e_s,
    D0  = SUM_k rho_k0 * stride_k,
    D_s = SUM_k rho_ks * stride_k,
    K   = CONSPART + D0,

so every quantity except the loop variables ``e_s`` is a compile-time
constant. ``stride_k = d_(k+1) * ... * d_n`` comes from the array information
vector. This module is pure arithmetic on the Phase 2 and Phase 7 records; it
emits no code.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .linear_analysis import LinearForm
from .symbol_table import ArrayInfo


@dataclass(frozen=True)
class AccessPlan:
    """Address plan of one optimizable access.

    ``ds`` has one ``(loop variable, D_s)`` entry per enclosing loop,
    outermost first, zeros included (a zero means the address does not depend
    on that loop); ``terms`` leaves the zeros out.
    """

    array: str
    conspart: int
    d0: int
    k: int
    ds: tuple[tuple[str, int], ...]

    @property
    def terms(self) -> tuple[tuple[str, int], ...]:
        """The ``(loop variable, D_s)`` pairs with ``D_s != 0``, outermost first."""
        return tuple((v, d) for v, d in self.ds if d != 0)

    def address(self, env: Mapping[str, int]) -> int:
        """``K + SUM D_s * e_s`` for the loop-variable values in ``env``."""
        return self.k + sum(d * env[v] for v, d in self.ds)

    def expr_text(self) -> str:
        """The address as text: ``998 + 40*i + j``, ``1199 - 20*i + j``, ``1043``."""
        text = str(self.k)
        for v, d in self.terms:
            if d == 1:
                text += f" + {v}"
            elif d == -1:
                text += f" - {v}"
            elif d < 0:
                text += f" - {-d}*{v}"
            else:
                text += f" + {d}*{v}"
        return text


def make_plan(info: ArrayInfo, forms: Sequence[LinearForm], loop_vars: Sequence[str]) -> AccessPlan:
    """Plan of an access to ``info`` with the linear subscript ``forms`` inside the loops ``loop_vars``.

    A loop variable missing from a form has coefficient 0 (design decision 81).
    Raises ``ValueError`` if the number of forms differs from the dimension count.
    """
    if len(forms) != info.n:
        raise ValueError(f"array {info.name!r} has {info.n} dimensions but {len(forms)} subscript forms were given")
    d0 = sum(f.const * s for f, s in zip(forms, info.strides))
    ds = tuple((v, sum(f.coeffs.get(v, 0) * s for f, s in zip(forms, info.strides))) for v in loop_vars)
    return AccessPlan(info.name, info.conspart, d0, info.conspart + d0, ds)
