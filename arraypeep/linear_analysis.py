"""Linear subscript analysis (paper Eq. 4): decides linearity and extracts the rho_ks.

A subscript is *linear* when it equals ``rho_k0 + SUM rho_ks * e_s`` where the
``e_s`` are the loop variables of the loops enclosing the access and every
``rho`` is an integer constant. ``analyze_subscript`` works on an expression
tree alone (it knows nothing about quadruples or code emission) and never
raises for an unsupported expression: it returns ``NonLinear`` with a reason.

Supported: integer literals, enclosing loop variables, ``+``, ``-``, unary
minus, a constant times an expression, an expression times a constant, and
parentheses (which leave no node). A product is linear only if one side has no
loop variable left after simplification (so ``2 * (3 - 1)`` and ``i * 0`` are
fine, ``i * j`` is not). Coefficients that cancel are dropped (``i - i`` is the
constant 0).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from . import ast_nodes as ast

REASON_PRODUCT = "product of loop variables"
REASON_SCALAR = "variable that is not a loop variable"
REASON_ARRAY = "array element in subscript"
REASON_UNSUPPORTED = "unsupported expression"


@dataclass(frozen=True)
class LinearForm:
    """``const + SUM coeffs[v] * v``; ``coeffs`` holds only non-zero entries, in loop order."""

    const: int
    coeffs: dict[str, int] = field(default_factory=dict)

    def evaluate(self, env: Mapping[str, int]) -> int:
        """Value of the form for the loop-variable values in ``env``."""
        return self.const + sum(c * env[v] for v, c in self.coeffs.items())

    def table_text(self) -> str:
        """Roadmap table form: ``1 ; i:2``, ``0 ; i:1, j:1``, ``5 ; (no loop variables)``."""
        if not self.coeffs:
            return f"{self.const} ; (no loop variables)"
        return f"{self.const} ; " + ", ".join(f"{v}:{c}" for v, c in self.coeffs.items())

    def expr_text(self) -> str:
        """Report form of quad_spec.md 9.3: ``1 + 2*i``, ``-1 + 1*j``, ``3 - 1*j``, ``5``."""
        text = str(self.const)
        for v, c in self.coeffs.items():
            text += f" - {-c}*{v}" if c < 0 else f" + {c}*{v}"
        return text


@dataclass(frozen=True)
class NonLinear:
    """A subscript that is not linear.

    ``reason`` is one of the ``REASON_*`` texts (exactly as in the roadmap
    table) and ``detail`` names the offending part (``k``, ``B[i]``, ``i * j``).
    """

    reason: str
    detail: str = ""

    @property
    def text(self) -> str:
        """``NOT LINEAR: <reason>`` as in the roadmap table."""
        return f"NOT LINEAR: {self.reason}"

    def describe(self) -> str:
        """``text`` followed by the offending part in parentheses, when known."""
        return f"{self.text} ({self.detail})" if self.detail else self.text


Result = LinearForm | NonLinear


def analyze_subscript(expr: ast.Expr, loop_vars: Sequence[str]) -> Result:
    """Linear form of ``expr`` in the loop variables ``loop_vars`` (outermost first), or ``NonLinear``."""
    order = {name: n for n, name in enumerate(loop_vars)}
    result = _analyze(expr, order)
    if isinstance(result, NonLinear):
        return result
    const, coeffs = result
    ordered = {v: coeffs[v] for v in sorted(coeffs, key=order.__getitem__) if coeffs[v] != 0}
    return LinearForm(const, ordered)


def _analyze(expr: ast.Expr, order: dict[str, int]) -> tuple[int, dict[str, int]] | NonLinear:
    if isinstance(expr, ast.Number):
        return expr.value, {}
    if isinstance(expr, ast.ScalarRef):
        if expr.name in order:
            return 0, {expr.name: 1}
        return NonLinear(REASON_SCALAR, expr.name)
    if isinstance(expr, ast.ArrayRef):
        return NonLinear(REASON_ARRAY, ast.format_expr(expr))
    if isinstance(expr, ast.UnaryMinus):
        inner = _analyze(expr.operand, order)
        if isinstance(inner, NonLinear):
            return inner
        return -inner[0], {v: -c for v, c in inner[1].items()}
    if isinstance(expr, ast.BinOp):
        left = _analyze(expr.left, order)
        if isinstance(left, NonLinear):
            return left
        right = _analyze(expr.right, order)
        if isinstance(right, NonLinear):
            return right
        if expr.op in ("+", "-"):
            sign = 1 if expr.op == "+" else -1
            coeffs = dict(left[1])
            for v, c in right[1].items():
                coeffs[v] = coeffs.get(v, 0) + sign * c
            return left[0] + sign * right[0], {v: c for v, c in coeffs.items() if c != 0}
        if expr.op == "*":
            lc, rc = {v: c for v, c in left[1].items() if c}, {v: c for v, c in right[1].items() if c}
            if lc and rc:
                return NonLinear(REASON_PRODUCT, ast.format_expr(expr))
            if not lc:  # left is a constant
                return left[0] * right[0], {v: left[0] * c for v, c in rc.items() if left[0] * c}
            return left[0] * right[0], {v: right[0] * c for v, c in lc.items() if right[0] * c}
    return NonLinear(REASON_UNSUPPORTED, type(expr).__name__)
