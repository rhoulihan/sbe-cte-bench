"""Tests for the timing math used in the run record."""

from __future__ import annotations

import math
from statistics import median

import pytest

from sbe_cte_bench.runner.timing import TimingDistribution, percentile, summarize


@pytest.mark.unit
def test_percentile_p50_matches_median() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 50) == pytest.approx(median(values))


@pytest.mark.unit
def test_percentile_p95_of_20_returns_19th() -> None:
    """At n=20, p95 corresponds to the 95th percentile via linear interpolation.

    For n=20, the 95th percentile lands between the 19th and 20th values.
    """
    values = [float(i) for i in range(1, 21)]  # [1.0..20.0]
    p95 = percentile(values, 95)
    assert 19.0 <= p95 <= 20.0


@pytest.mark.unit
def test_percentile_p100_is_max() -> None:
    values = [10.0, 20.0, 30.0]
    assert percentile(values, 100) == 30.0


@pytest.mark.unit
def test_percentile_p0_is_min() -> None:
    values = [10.0, 20.0, 30.0]
    assert percentile(values, 0) == 10.0


@pytest.mark.unit
def test_percentile_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        percentile([], 50)


@pytest.mark.unit
def test_percentile_invalid_q_raises() -> None:
    with pytest.raises(ValueError):
        percentile([1.0, 2.0], 101)
    with pytest.raises(ValueError):
        percentile([1.0, 2.0], -1)


@pytest.mark.unit
def test_summarize_distribution_returns_typed_record() -> None:
    values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
    summary = summarize(values)
    assert isinstance(summary, TimingDistribution)
    assert summary.n == 10
    assert summary.median_ms == pytest.approx(14.5)
    assert summary.min_ms == 10.0
    assert summary.max_ms == 19.0


@pytest.mark.unit
def test_summarize_includes_iqr() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    summary = summarize(values)
    # Linear interpolation: p25 = 3.25, p75 = 7.75 → IQR ≈ 4.5
    assert summary.iqr_ms == pytest.approx(4.5, rel=0.05)


@pytest.mark.unit
def test_summarize_includes_cv() -> None:
    """CV = stddev / mean, used to flag noisy runs."""
    values = [10.0] * 10  # zero variance
    summary = summarize(values)
    assert summary.cv == 0.0

    noisy = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    nsum = summarize(noisy)
    assert nsum.cv > 0


@pytest.mark.unit
def test_summarize_p99_at_n20_flagged_low_confidence() -> None:
    """At n<100, p99 is reported but flagged as low-confidence per spec."""
    values = [float(i) for i in range(20)]
    summary = summarize(values)
    assert summary.p99_low_confidence is True


@pytest.mark.unit
def test_summarize_serializable_to_dict() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    summary = summarize(values)
    d = summary.to_dict()
    assert "median_ms" in d
    assert "p95_ms" in d
    assert "iqr_ms" in d
    assert "cv" in d
    assert "n" in d


@pytest.mark.unit
def test_summarize_does_not_report_mean() -> None:
    """Per docs/01-methodology.md, we do NOT report mean - only median.

    Means are misleading for query timings; a single GC pause inflates the
    mean while leaving the median stable.
    """
    values = [1.0, 2.0, 3.0]
    summary = summarize(values)
    d = summary.to_dict()
    assert "mean_ms" not in d


@pytest.mark.unit
def test_cv_threshold_flag_at_0_10() -> None:
    """The methodology flags runs with cv > 0.10 for re-execution."""
    from sbe_cte_bench.runner.timing import is_noisy_run

    stable = [10.0, 10.1, 9.9, 10.05, 9.95]
    assert not is_noisy_run(summarize(stable))

    noisy = [10.0, 100.0, 5.0, 200.0, 1.0]
    assert is_noisy_run(summarize(noisy))


@pytest.mark.unit
def test_summarize_single_value() -> None:
    summary = summarize([42.0])
    assert summary.median_ms == 42.0
    assert summary.min_ms == 42.0
    assert summary.max_ms == 42.0
    assert summary.cv == 0.0
    assert summary.iqr_ms == 0.0


@pytest.mark.unit
def test_summarize_all_finite() -> None:
    summary = summarize([1.0, 2.0, 3.0])
    for value in (
        summary.median_ms,
        summary.p95_ms,
        summary.p99_ms,
        summary.min_ms,
        summary.max_ms,
        summary.iqr_ms,
        summary.cv,
    ):
        assert math.isfinite(value)
