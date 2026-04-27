"""Parse ``dbms_xplan.display_cursor`` text output.

This is the architectural counterpart to ``mongo_explain.py`` — it answers
"what did Oracle's CBO actually do with the inlined CTEs we wrote?". Specific
extracts:

- Plan hash value (for cross-iteration plan-stability checks).
- SQL_ID.
- Per-step operation, name, row estimate, cost.
- Detection of ``TEMP TABLE TRANSFORMATION`` — Oracle's signal that a CTE was
  materialized rather than inlined. The benchmark spec depends on the default
  inlining behavior; if a query unexpectedly materializes, it's a finding.

The parser is text-based because ``dbms_xplan.display_cursor`` is the de-facto
standard format that everyone (DBAs, monitoring, our harness) reads. The
``v$sql_plan`` view exists for a structured alternative but it's lossier (no
predicates section, no notes) and harder to capture cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_SQL_ID_RE = re.compile(r"^SQL_ID\s+(\S+),\s+child number (\d+)", re.MULTILINE)
_PLAN_HASH_RE = re.compile(r"^Plan hash value:\s+(\d+)", re.MULTILINE)
# Match plan rows: | Id  | Operation | Name | Rows | Bytes | Cost ... |
# IDs may be prefixed with `*` (predicate marker) or have leading whitespace.
_ROW_RE = re.compile(
    r"^\|\s*\*?\s*(\d+)\s*\|"  # id
    r"\s*([A-Z][A-Z _()]*?[A-Z)])\s*\|"  # operation (uppercase words/spaces)
    r"\s*([^|]*?)\s*\|"  # name
    r"\s*([\d.KMG]+|)\s*\|"  # rows
    r".*\|$",  # rest of the row (bytes, cost, etc.)
    re.MULTILINE,
)


@dataclass(frozen=True)
class PlanOperation:
    op_id: int
    operation: str
    name: str | None
    rows: int | None


@dataclass(frozen=True)
class XplanSummary:
    sql_id: str | None
    plan_hash: int | None
    operations: list[PlanOperation] = field(default_factory=list)
    has_materialized_ctes: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql_id": self.sql_id,
            "plan_hash": self.plan_hash,
            "has_materialized_ctes": self.has_materialized_ctes,
            "operations": [
                {
                    "op_id": op.op_id,
                    "operation": op.operation,
                    "name": op.name,
                    "rows": op.rows,
                }
                for op in self.operations
            ],
        }


def parse_xplan(text: str) -> XplanSummary:
    """Distil dbms_xplan output into an :class:`XplanSummary`."""
    sql_id_match = _SQL_ID_RE.search(text)
    plan_hash_match = _PLAN_HASH_RE.search(text)

    operations: list[PlanOperation] = []
    has_materialize = False
    for row_match in _ROW_RE.finditer(text):
        op_id = int(row_match[1])
        operation = row_match.group(2).strip()
        name_raw = row_match.group(3).strip()
        name = name_raw if name_raw else None
        rows = _parse_rows(row_match.group(4).strip())
        operations.append(PlanOperation(op_id=op_id, operation=operation, name=name, rows=rows))
        if "TEMP TABLE TRANSFORMATION" in operation:
            has_materialize = True

    return XplanSummary(
        sql_id=sql_id_match.group(1) if sql_id_match else None,
        plan_hash=int(plan_hash_match.group(1)) if plan_hash_match else None,
        operations=operations,
        has_materialized_ctes=has_materialize,
    )


def _parse_rows(token: str) -> int | None:
    """Parse Oracle's row-count column ('100', '1000K', '1000M')."""
    if not token:
        return None
    multiplier = 1
    suffix_chars = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}
    if token[-1] in suffix_chars:
        multiplier = suffix_chars[token[-1]]
        token = token[:-1]
    try:
        return int(float(token) * multiplier)
    except ValueError:
        return None
