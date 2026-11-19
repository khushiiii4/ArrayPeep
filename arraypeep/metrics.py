"""Counter data structures, comparison tables and improvement ratios.

Phase 6 part: the dynamic counters of one run (docs/quad_spec.md Section 8).
Comparison tables and improvement ratios are added in Phase 12.

``Counters`` counts *executed* quadruples: in total, per opcode and per tag
(``ADDR`` / ``BODY``). The special counts of the roadmap (multiplications,
additions, subtractions, loads, stores) are derived from the per-opcode and
per-tag tables, so they can never disagree with them. Unary minus is a
subtraction (``-, 0, x, T``) and counts as one.
"""
from dataclasses import dataclass, field

from .quad import OP_LOAD, OP_STORE, TAG_ADDR, TAG_BODY


@dataclass
class Counters:
    """Dynamic counters of one run; ``static`` is the length of the executed listing."""

    static: int = 0
    total: int = 0
    by_op: dict[str, int] = field(default_factory=dict)
    by_tag: dict[str, int] = field(default_factory=lambda: {TAG_ADDR: 0, TAG_BODY: 0})
    by_op_tag: dict[tuple[str, str], int] = field(default_factory=dict)

    def record(self, op: str, tag: str) -> None:
        """Count one executed quadruple."""
        self.total += 1
        self.by_op[op] = self.by_op.get(op, 0) + 1
        self.by_tag[tag] += 1
        key = (op, tag)
        self.by_op_tag[key] = self.by_op_tag.get(key, 0) + 1

    # -- derived counts ---------------------------------------------------------

    def op(self, name: str) -> int:
        """Executed quadruples with opcode ``name``."""
        return self.by_op.get(name, 0)

    @property
    def multiplications(self) -> int:
        """Executed ``*`` quadruples."""
        return self.op("*")

    @property
    def additions(self) -> int:
        """Executed ``+`` quadruples."""
        return self.op("+")

    @property
    def subtractions(self) -> int:
        """Executed ``-`` quadruples."""
        return self.op("-")

    @property
    def add_sub(self) -> int:
        """Executed ``+`` and ``-`` quadruples."""
        return self.additions + self.subtractions

    @property
    def loads(self) -> int:
        """Executed array-read quadruples."""
        return self.op(OP_LOAD)

    @property
    def stores(self) -> int:
        """Executed array-write quadruples."""
        return self.op(OP_STORE)

    @property
    def addr(self) -> int:
        """Executed quadruples tagged ``ADDR`` (dynamic address work)."""
        return self.by_tag[TAG_ADDR]

    @property
    def body(self) -> int:
        """Executed quadruples tagged ``BODY`` (the program's own work)."""
        return self.by_tag[TAG_BODY]

    @property
    def addr_multiplications(self) -> int:
        """Executed ``*`` quadruples tagged ``ADDR``."""
        return self.by_op_tag.get(("*", TAG_ADDR), 0)

    @property
    def addr_add_sub(self) -> int:
        """Executed ``+`` and ``-`` quadruples tagged ``ADDR``."""
        return self.by_op_tag.get(("+", TAG_ADDR), 0) + self.by_op_tag.get(("-", TAG_ADDR), 0)

    def format(self) -> str:
        """Short multi-line text of the counters (no trailing newline)."""
        ops = ", ".join(f"{op} {n}" for op, n in sorted(self.by_op.items()))
        return "\n".join(
            [
                f"static quadruples   {self.static}",
                f"dynamic quadruples  {self.total}   (ADDR {self.addr}, BODY {self.body})",
                f"multiplications     {self.multiplications}   (ADDR {self.addr_multiplications})",
                f"additions/subtractions {self.add_sub}   (ADDR {self.addr_add_sub})",
                f"loads {self.loads}   stores {self.stores}",
                f"per opcode          {ops}",
            ]
        )
