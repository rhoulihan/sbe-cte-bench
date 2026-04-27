"""Tests for the MongoDB spill metrics parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sbe_cte_bench.observability.spill_metrics import parse_profile_entry

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "profile"


@pytest.mark.unit
def test_parse_group_spill_extracted() -> None:
    entry = json.loads((_FIXTURES / "group_spill.json").read_text())
    metrics = parse_profile_entry(entry)
    assert metrics.has_spill is True
    assert metrics.per_stage["group"].spill_count == 5
    assert metrics.per_stage["group"].spilled_bytes == 104857600
    assert metrics.per_stage["group"].spilled_records == 80000


@pytest.mark.unit
def test_parse_sort_spill_extracted() -> None:
    entry = json.loads((_FIXTURES / "group_spill.json").read_text())
    metrics = parse_profile_entry(entry)
    assert "sort" in metrics.per_stage
    assert metrics.per_stage["sort"].spill_count == 2
    assert metrics.per_stage["sort"].spilled_bytes == 20971520


@pytest.mark.unit
def test_parse_no_spill_returns_empty_per_stage() -> None:
    entry = json.loads((_FIXTURES / "no_spill.json").read_text())
    metrics = parse_profile_entry(entry)
    assert metrics.has_spill is False
    assert metrics.per_stage == {}


@pytest.mark.unit
def test_parse_handles_missing_optional_counters() -> None:
    entry = {"groupSpills": 1, "groupSpilledBytes": 100}
    metrics = parse_profile_entry(entry)
    assert metrics.per_stage["group"].spilled_records == 0


@pytest.mark.unit
def test_legacy_used_disk_flag_recognized() -> None:
    """Pre-8.1 mongod emits a single `usedDisk: true` flag without per-stage detail."""
    entry = {"usedDisk": True, "millis": 5000}
    metrics = parse_profile_entry(entry)
    assert metrics.has_spill is True


@pytest.mark.unit
def test_total_spilled_bytes_aggregates_across_stages() -> None:
    entry = {
        "groupSpilledBytes": 100,
        "sortSpilledBytes": 200,
        "setWindowFieldsSpilledBytes": 300,
    }
    metrics = parse_profile_entry(entry)
    assert metrics.total_spilled_bytes() == 600


@pytest.mark.unit
def test_serializable_to_dict() -> None:
    """SpillMetrics serializes for inclusion in the JSON run record."""
    entry = json.loads((_FIXTURES / "group_spill.json").read_text())
    metrics = parse_profile_entry(entry)
    d = metrics.to_dict()
    assert isinstance(d, dict)
    assert d["per_stage"]["group"]["spill_count"] == 5
