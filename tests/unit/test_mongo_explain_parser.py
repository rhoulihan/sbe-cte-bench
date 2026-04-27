"""Tests for the MongoDB explain output parser.

The parser's job is to identify the SBE → classic engine boundary in a
multi-stage aggregation pipeline. The boundary is signaled by a ``$cursor``
wrapper appearing on a stage other than stage 0 — at that point, MongoDB has
re-materialized the documents into BSON for the classic engine to consume.
This is the architectural cliff S03 measures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sbe_cte_bench.observability.mongo_explain import ExplainSummary, parse_explain

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "explain"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text())  # type: ignore[no-any-return]


@pytest.mark.unit
def test_parse_sbe_all_stages() -> None:
    """A pipeline running entirely in SBE has the boundary at index 0 only."""
    explain = _load("sbe_all_stages.json")
    summary = parse_explain(explain)
    assert isinstance(summary, ExplainSummary)
    assert summary.sbe_prefix_length == 3  # all stages SBE-pushed
    assert summary.classic_boundary_at_stage is None
    assert summary.uses_express_path is False


@pytest.mark.unit
def test_parse_boundary_at_stage_4() -> None:
    """A pipeline whose 4th stage (index 3) is `$bucketAuto` shows boundary at 3."""
    explain = _load("sbe_classic_boundary_at_4.json")
    summary = parse_explain(explain)
    # $bucketAuto at index 3 is the first non-SBE stage.
    assert summary.classic_boundary_at_stage == 3
    assert summary.sbe_prefix_length == 3


@pytest.mark.unit
def test_parse_express_path_detected() -> None:
    explain = _load("express_path.json")
    summary = parse_explain(explain)
    assert summary.uses_express_path is True


@pytest.mark.unit
def test_parse_extracts_winning_plan_index_name() -> None:
    explain = _load("sbe_all_stages.json")
    summary = parse_explain(explain)
    assert summary.winning_index_name == "IX_ORD_CUST_DATE"


@pytest.mark.unit
def test_parse_extracts_per_stage_timing() -> None:
    explain = _load("sbe_classic_boundary_at_4.json")
    summary = parse_explain(explain)
    assert len(summary.per_stage_time_ms) == 6
    # Stage 0 ($match/$cursor) has the engine-side estimate
    assert summary.per_stage_time_ms[0] == 50
    # $bucketAuto at index 3
    assert summary.per_stage_time_ms[3] == 40


@pytest.mark.unit
def test_parse_extracts_total_docs_examined() -> None:
    explain = _load("sbe_all_stages.json")
    summary = parse_explain(explain)
    assert summary.total_docs_examined == 800
    assert summary.total_keys_examined == 1000


@pytest.mark.unit
def test_parse_extracts_server_version() -> None:
    explain = _load("sbe_all_stages.json")
    summary = parse_explain(explain)
    assert summary.server_version == "8.2.2"


@pytest.mark.unit
def test_parse_handles_missing_optional_fields() -> None:
    """The parser is forgiving on missing fields (drivers vary version-to-version)."""
    minimal = {"stages": [], "serverInfo": {"version": "8.2.2"}}
    summary = parse_explain(minimal)
    assert summary.sbe_prefix_length == 0
    assert summary.classic_boundary_at_stage is None


@pytest.mark.unit
def test_parse_collscan_winning_plan() -> None:
    """COLLSCAN winning plans are flagged."""
    explain = {
        "stages": [
            {
                "$cursor": {
                    "queryPlanner": {"winningPlan": {"queryPlan": {"stage": "COLLSCAN"}}},
                    "executionStats": {
                        "totalDocsExamined": 1000,
                        "totalKeysExamined": 0,
                        "nReturned": 1000,
                    },
                }
            }
        ],
        "serverInfo": {"version": "8.2.2"},
    }
    summary = parse_explain(explain)
    assert summary.winning_stage == "COLLSCAN"
    assert summary.winning_index_name is None
