"""Tests for the run record schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from sbe_cte_bench.config.run_record import (
    SCHEMA_VERSION,
    EquivalenceBlock,
    RunRecord,
)


def _minimal_record_dict() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": "test-run-1",
        "timestamp": datetime(2026, 4, 25, tzinfo=UTC).isoformat(),
        "scenario": "S01",
        "scenario_title": "Baseline",
        "variant": {"scale_factor": "SF0.001"},
        "host": {
            "kernel": "6.8.0-test",
            "cpu_model": "AMD EPYC 7402P",
            "physical_cores": 16,
            "memory_gb": 128,
            "storage": "Samsung 980 PRO 1TB NVMe",
        },
        "mongo": {
            "version": "8.2.2",
            "framework_control": "trySbeEngine",
            "wt_cache_gb": 1.5,
            "pipeline": [],
            "explain": {},
            "spill": {},
            "timings_ms": [10.0, 11.0, 12.0],
            "median_ms": 11.0,
            "p95_ms": 11.9,
            "p99_ms": 11.98,
            "min_ms": 10.0,
            "max_ms": 12.0,
            "iqr_ms": 1.0,
            "cv": 0.05,
            "n": 3,
            "p99_low_confidence": True,
            "errors": [],
        },
        "oracle": {
            "version": "26.0.0.0",
            "sga_mb": 1200,
            "pga_mb": 600,
            "sql": "SELECT 1 FROM dual",
            "plan": {},
            "workarea": {},
            "statspack": {},
            "timings_ms": [9.0, 10.0, 11.0],
            "median_ms": 10.0,
            "p95_ms": 10.9,
            "p99_ms": 10.98,
            "min_ms": 9.0,
            "max_ms": 11.0,
            "iqr_ms": 1.0,
            "cv": 0.05,
            "n": 3,
            "p99_low_confidence": True,
            "errors": [],
        },
        "equivalence": {
            "mongo_hash": "abc123",
            "oracle_hash": "abc123",
            "match": True,
            "row_count_mongo": 100,
            "row_count_oracle": 100,
        },
        "prediction": {
            "claim": "ratio in [0.8, 1.3]",
            "expected": {"metric": "ratio", "operator": "in", "value": [0.8, 1.3]},
            "observed": {"metric": "ratio", "value": 1.1},
            "pass": True,
        },
    }


@pytest.mark.unit
def test_minimal_record_validates() -> None:
    record = RunRecord.model_validate(_minimal_record_dict())
    assert record.scenario == "S01"


@pytest.mark.unit
def test_record_round_trips_through_json() -> None:
    original = _minimal_record_dict()
    record = RunRecord.model_validate(original)
    payload = record.model_dump_json()
    re_parsed = RunRecord.model_validate_json(payload)
    assert re_parsed.scenario == record.scenario


@pytest.mark.unit
def test_extra_fields_rejected() -> None:
    """Extra top-level fields are rejected to prevent silent schema drift."""
    bad = _minimal_record_dict()
    bad["unrelated"] = "field"
    with pytest.raises(ValidationError):
        RunRecord.model_validate(bad)


@pytest.mark.unit
def test_prediction_pass_field_aliased() -> None:
    """The prediction `pass` field round-trips via the alias."""
    record = RunRecord.model_validate(_minimal_record_dict())
    payload = record.model_dump(by_alias=True)
    assert payload["prediction"]["pass"] is True


@pytest.mark.unit
def test_schema_version_pinned() -> None:
    bad = _minimal_record_dict()
    bad["schema_version"] = "0.5"
    with pytest.raises(ValidationError):
        RunRecord.model_validate(bad)


@pytest.mark.unit
def test_equivalence_block_match_required() -> None:
    block = EquivalenceBlock(
        mongo_hash="x",
        oracle_hash="x",
        match=True,
        row_count_mongo=0,
        row_count_oracle=0,
    )
    assert block.match is True
