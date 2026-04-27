"""Parse MongoDB ``aggregate({explain: ...})`` output.

The fundamental architectural claim under test in S03 is that a pipeline's
SBE-pushed prefix ends at the first stage MongoDB cannot lower into SBE — and
from that point onward, classic-engine BSON materialization runs per row per
stage. The ``$cursor`` wrapper in the explain output marks the boundary.

Parser responsibilities:

- Identify ``classic_boundary_at_stage``: the index of the first stage where
  classic execution takes over (None when the entire pipeline runs in SBE).
- Identify ``sbe_prefix_length``: how many stages ran in SBE.
- Detect Express Path (``EXPRESS_IXSCAN`` / ``EXPRESS_CLUSTERED_IXSCAN``).
- Extract winning plan index name and stage type.
- Extract per-stage timing estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXPRESS_PATH_STAGES = {"EXPRESS_IXSCAN", "EXPRESS_CLUSTERED_IXSCAN"}


@dataclass(frozen=True)
class ExplainSummary:
    """Distilled view of a MongoDB explain output, suitable for a run record."""

    sbe_prefix_length: int
    classic_boundary_at_stage: int | None
    winning_stage: str | None
    winning_index_name: str | None
    uses_express_path: bool
    per_stage_time_ms: list[int]
    total_docs_examined: int
    total_keys_examined: int
    server_version: str


def parse_explain(explain: dict[str, Any]) -> ExplainSummary:
    """Distil a MongoDB explain payload into an :class:`ExplainSummary`."""
    stages: list[dict[str, Any]] = list(explain.get("stages", []))
    server_version = (explain.get("serverInfo") or {}).get("version", "unknown")

    classic_boundary = _detect_classic_boundary(stages)
    sbe_prefix_length = classic_boundary if classic_boundary is not None else len(stages)

    winning_plan_dict = _winning_plan(stages)
    winning_stage = _winning_stage(winning_plan_dict)
    winning_index = _winning_index_name(winning_plan_dict)
    express = winning_stage in EXPRESS_PATH_STAGES if winning_stage else False

    per_stage_ms = _per_stage_timing(stages)
    docs_examined, keys_examined = _examined_counters(stages)

    return ExplainSummary(
        sbe_prefix_length=sbe_prefix_length,
        classic_boundary_at_stage=classic_boundary,
        winning_stage=winning_stage,
        winning_index_name=winning_index,
        uses_express_path=express,
        per_stage_time_ms=per_stage_ms,
        total_docs_examined=docs_examined,
        total_keys_examined=keys_examined,
        server_version=server_version,
    )


def _detect_classic_boundary(stages: list[dict[str, Any]]) -> int | None:
    """Return the index of the first stage with a classic-engine ``$cursor``.

    The conventional SBE-pushed pipeline carries a ``$cursor`` wrapper only at
    stage 0 (where the IXSCAN/COLLSCAN feeds into the lowered SBE plan). When
    a downstream stage *also* has a ``$cursor``, that's the materialization
    boundary — classic engine begins there.

    Per ``docs/06-instrumentation.md`` and the article's Part 2.
    """
    for i, stage in enumerate(stages[1:], start=1):
        if "$cursor" in stage:
            return i
    return None


def _winning_plan(stages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not stages:
        return None
    cursor = stages[0].get("$cursor", {})
    qp = cursor.get("queryPlanner", {})
    plan = qp.get("winningPlan")
    return plan if isinstance(plan, dict) else None


def _winning_stage(winning_plan: dict[str, Any] | None) -> str | None:
    if not winning_plan:
        return None
    # SBE-style winning plan has ``queryPlan.stage``; legacy classic has
    # ``stage`` directly.
    qp = winning_plan.get("queryPlan", {})
    return qp.get("stage") or winning_plan.get("stage")


def _winning_index_name(winning_plan: dict[str, Any] | None) -> str | None:
    if not winning_plan:
        return None
    qp = winning_plan.get("queryPlan", {}) or winning_plan
    # Walk inputStage chain looking for an indexName.
    node: dict[str, Any] | None = qp
    while isinstance(node, dict):
        if "indexName" in node:
            value = node["indexName"]
            return value if isinstance(value, str) else None
        node = node.get("inputStage")
    return None


def _per_stage_timing(stages: list[dict[str, Any]]) -> list[int]:
    """Extract per-stage timing.

    Stages that wrap a ``$cursor`` may carry their timing either inside
    ``$cursor.executionStats`` (the SBE-pushed prefix at stage 0) or at the
    stage top level (downstream classic stages emit ``executionTimeMillisEstimate``
    next to the stage operator). Try both.
    """
    timings: list[int] = []
    for stage in stages:
        # Stage-level field takes precedence for downstream classic stages.
        stage_level = stage.get("executionTimeMillisEstimate")
        if stage_level is not None:
            timings.append(int(stage_level))
            continue
        cursor = stage.get("$cursor", {})
        stats = cursor.get("executionStats", {}) if isinstance(cursor, dict) else {}
        timings.append(int(stats.get("executionTimeMillisEstimate", 0)))
    return timings


def _examined_counters(stages: list[dict[str, Any]]) -> tuple[int, int]:
    if not stages:
        return 0, 0
    cursor = stages[0].get("$cursor", {})
    stats = cursor.get("executionStats", {})
    return (
        int(stats.get("totalDocsExamined", 0)),
        int(stats.get("totalKeysExamined", 0)),
    )
