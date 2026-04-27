"""Tests for chart generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbe_cte_bench.reporting.plot import latency_vs_variant_chart, set_deterministic_rcparams


@pytest.mark.unit
def test_set_deterministic_rcparams_idempotent() -> None:
    set_deterministic_rcparams()
    set_deterministic_rcparams()  # second call must not raise


@pytest.mark.unit
def test_latency_vs_variant_chart_writes_svg(tmp_path: Path) -> None:
    out = tmp_path / "S03.svg"
    latency_vs_variant_chart(
        title="S03 boundary tax",
        variant_axis="boundary_position",
        variant_labels=["k=2", "k=4", "k=6", "k=8"],
        mongo_medians=[800, 580, 380, 200],
        oracle_medians=[40, 39, 38, 37],
        output=out,
    )
    assert out.exists()
    assert out.read_text().startswith("<?xml") or out.read_text().startswith("<svg")


@pytest.mark.unit
def test_latency_chart_handles_empty_input(tmp_path: Path) -> None:
    out = tmp_path / "empty.svg"
    with pytest.raises(ValueError, match="empty"):
        latency_vs_variant_chart(
            title="empty",
            variant_axis="x",
            variant_labels=[],
            mongo_medians=[],
            oracle_medians=[],
            output=out,
        )


@pytest.mark.unit
def test_latency_chart_validates_input_lengths(tmp_path: Path) -> None:
    out = tmp_path / "mismatch.svg"
    with pytest.raises(ValueError, match="length"):
        latency_vs_variant_chart(
            title="mismatch",
            variant_axis="x",
            variant_labels=["a", "b"],
            mongo_medians=[1.0],
            oracle_medians=[2.0, 3.0],
            output=out,
        )
