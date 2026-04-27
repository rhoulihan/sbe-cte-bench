"""Tests for the cross-scenario report generator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sbe_cte_bench.reporting.cross_scenario import (
    collect_latest_per_variant,
    render_report,
    write_report,
)


def _make_record(
    scenario: str,
    variant_label: str,
    *,
    mongo_median: float,
    oracle_median: float,
    timestamp: datetime | None = None,
    pred_pass: bool = False,
    eq_match: bool = True,
) -> dict[str, Any]:
    ts = timestamp or datetime(2026, 4, 25, tzinfo=UTC)
    return {
        "schema_version": "1.0",
        "run_id": f"{scenario}-{variant_label}-test",
        "timestamp": ts.isoformat(),
        "scenario": scenario,
        "scenario_title": f"{scenario} title",
        "variant": {"label": variant_label},
        "host": {
            "kernel": "test",
            "cpu_model": "test",
            "physical_cores": 2,
            "memory_gb": 4,
            "storage": "nvme",
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
            "cv": 0.05,
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
            "cv": 0.05,
            "n": 1,
            "p99_low_confidence": True,
            "errors": [],
        },
        "equivalence": {
            "mongo_hash": "x",
            "oracle_hash": "x" if eq_match else "y",
            "match": eq_match,
            "row_count_mongo": 100,
            "row_count_oracle": 100,
        },
        "prediction": {
            "claim": "test claim",
            "expected": {"metric": "ratio", "operator": ">=", "value": 1.0},
            "observed": {"metric": "ratio", "value": mongo_median / oracle_median},
            "pass": pred_pass,
        },
    }


@pytest.mark.unit
def test_collect_latest_per_variant_dedupes_by_scenario_and_variant(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.json").write_text(
        json.dumps(
            _make_record(
                "S01",
                "default",
                mongo_median=10.0,
                oracle_median=8.0,
                timestamp=datetime(2026, 4, 25, 10, 0, tzinfo=UTC),
            )
        )
    )
    (raw / "b.json").write_text(
        json.dumps(
            _make_record(
                "S01",
                "default",
                mongo_median=20.0,
                oracle_median=8.0,
                timestamp=datetime(2026, 4, 25, 11, 0, tzinfo=UTC),  # later
            )
        )
    )
    rows = collect_latest_per_variant(raw)
    assert len(rows) == 1
    assert rows[0].mongo_median_ms == 20.0  # latest one wins


@pytest.mark.unit
def test_collect_includes_distinct_variants(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.json").write_text(
        json.dumps(_make_record("S03", "k=0", mongo_median=10, oracle_median=5))
    )
    (raw / "b.json").write_text(
        json.dumps(_make_record("S03", "k=4", mongo_median=20, oracle_median=5))
    )
    rows = collect_latest_per_variant(raw)
    assert len(rows) == 2


@pytest.mark.unit
def test_collect_skips_invalid_json(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "valid.json").write_text(
        json.dumps(_make_record("S01", "default", mongo_median=10, oracle_median=8))
    )
    (raw / "invalid.json").write_text("{not valid json")
    rows = collect_latest_per_variant(raw)
    assert len(rows) == 1


@pytest.mark.unit
def test_render_report_includes_headline_table(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.json").write_text(
        json.dumps(_make_record("S01", "default", mongo_median=10, oracle_median=8))
    )
    (raw / "b.json").write_text(
        json.dumps(_make_record("S03", "k=8", mongo_median=50, oracle_median=5, pred_pass=True))
    )
    rows = collect_latest_per_variant(raw)
    text = render_report(rows)
    assert "# sbe-cte-bench" in text
    assert "S01" in text
    assert "S03" in text
    assert "Predictions confirmed" in text  # at least one PASS triggers section
    assert "10.00" in text
    assert "5.00" in text


@pytest.mark.unit
def test_render_report_handles_empty_input() -> None:
    text = render_report([])
    assert "No run records" in text


@pytest.mark.unit
def test_render_report_includes_top_and_bottom_ratios(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    for i, ratio_target in enumerate([0.5, 1.0, 5.0, 10.0, 50.0]):
        (raw / f"r{i}.json").write_text(
            json.dumps(
                _make_record(
                    f"S{i:02d}",
                    "default",
                    mongo_median=ratio_target * 10,
                    oracle_median=10.0,
                )
            )
        )
    rows = collect_latest_per_variant(raw)
    text = render_report(rows)
    assert "Oracle wins" in text
    assert "Mongo wins" in text
    assert "50.00×" in text  # top ratio
    assert "0.50×" in text  # bottom ratio


@pytest.mark.unit
def test_write_report_creates_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.json").write_text(
        json.dumps(_make_record("S01", "default", mongo_median=10, oracle_median=8))
    )
    output = tmp_path / "REPORT.md"
    result = write_report(raw, output)
    assert result == output
    assert output.exists()
    assert output.read_text().startswith("# sbe-cte-bench")


@pytest.mark.unit
def test_render_includes_equivalence_status(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "match.json").write_text(
        json.dumps(_make_record("S01", "default", mongo_median=10, oracle_median=8, eq_match=True))
    )
    (raw / "miss.json").write_text(
        json.dumps(_make_record("S03", "k=4", mongo_median=20, oracle_median=5, eq_match=False))
    )
    rows = collect_latest_per_variant(raw)
    text = render_report(rows)
    # Equivalence MATCH count appears in headline
    assert "1/2" in text or "1 / 2" in text or "50%" in text
