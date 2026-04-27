"""Tests for the run-record aggregator."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sbe_cte_bench.reporting.aggregate import aggregate_runs


def _make_run_record(scenario: str, *, mongo_median: float, oracle_median: float) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": f"{scenario}-test",
        "timestamp": datetime(2026, 4, 25, tzinfo=UTC).isoformat(),
        "scenario": scenario,
        "scenario_title": "Test scenario",
        "variant": {"scale_factor": "SF0.001"},
        "host": {
            "kernel": "6.8",
            "cpu_model": "Test",
            "physical_cores": 2,
            "memory_gb": 4,
            "storage": "NVMe",
        },
        "mongo": {
            "version": "8.2.2",
            "framework_control": "trySbeEngine",
            "wt_cache_gb": 1.5,
            "pipeline": [],
            "explain": {},
            "spill": {},
            "timings_ms": [mongo_median],
            "median_ms": mongo_median,
            "p95_ms": mongo_median,
            "p99_ms": mongo_median,
            "min_ms": mongo_median,
            "max_ms": mongo_median,
            "iqr_ms": 0.0,
            "cv": 0.0,
            "n": 1,
            "p99_low_confidence": True,
            "errors": [],
        },
        "oracle": {
            "version": "26.0.0.0",
            "sga_mb": 1200,
            "pga_mb": 600,
            "sql": "SELECT 1",
            "plan": {},
            "workarea": {},
            "statspack": {},
            "timings_ms": [oracle_median],
            "median_ms": oracle_median,
            "p95_ms": oracle_median,
            "p99_ms": oracle_median,
            "min_ms": oracle_median,
            "max_ms": oracle_median,
            "iqr_ms": 0.0,
            "cv": 0.0,
            "n": 1,
            "p99_low_confidence": True,
            "errors": [],
        },
        "equivalence": {
            "mongo_hash": "x",
            "oracle_hash": "x",
            "match": True,
            "row_count_mongo": 1,
            "row_count_oracle": 1,
        },
        "prediction": {
            "claim": "test",
            "expected": {"metric": "ratio", "value": 1.0},
            "observed": {"metric": "ratio", "value": mongo_median / oracle_median},
            "pass": True,
        },
    }


@pytest.mark.unit
def test_aggregate_writes_summary_csv(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S01.json").write_text(
        json.dumps(_make_run_record("S01", mongo_median=10.0, oracle_median=8.0))
    )
    (raw_dir / "S02.json").write_text(
        json.dumps(_make_run_record("S02", mongo_median=20.0, oracle_median=10.0))
    )

    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)

    rows = list(csv.DictReader(summary_csv.open()))
    assert len(rows) == 2
    scenarios = {r["scenario"] for r in rows}
    assert scenarios == {"S01", "S02"}


@pytest.mark.unit
def test_aggregate_includes_ratio_column(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S01.json").write_text(
        json.dumps(_make_run_record("S01", mongo_median=20.0, oracle_median=10.0))
    )

    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)

    row = next(csv.DictReader(summary_csv.open()))
    assert float(row["ratio_mongo_to_oracle"]) == pytest.approx(2.0)


@pytest.mark.unit
def test_aggregate_includes_prediction_pass(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S01.json").write_text(
        json.dumps(_make_run_record("S01", mongo_median=10.0, oracle_median=10.0))
    )

    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)

    row = next(csv.DictReader(summary_csv.open()))
    assert row["prediction_pass"] == "True"


@pytest.mark.unit
def test_aggregate_skips_non_json_files(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S01.json").write_text(
        json.dumps(_make_run_record("S01", mongo_median=10.0, oracle_median=8.0))
    )
    (raw_dir / "README.md").write_text("not a run record")

    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)

    rows = list(csv.DictReader(summary_csv.open()))
    assert len(rows) == 1


@pytest.mark.unit
def test_aggregate_handles_empty_directory(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)
    assert summary_csv.exists()
    rows = list(csv.DictReader(summary_csv.open()))
    assert rows == []


@pytest.mark.unit
def test_aggregate_skips_invalid_records_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "valid.json").write_text(
        json.dumps(_make_run_record("S01", mongo_median=10.0, oracle_median=8.0))
    )
    (raw_dir / "invalid.json").write_text('{"not": "a run record"}')

    summary_csv = tmp_path / "summary.csv"
    aggregate_runs(raw_dir, summary_csv)

    captured = capsys.readouterr()
    assert "invalid.json" in captured.err

    rows = list(csv.DictReader(summary_csv.open()))
    assert len(rows) == 1
