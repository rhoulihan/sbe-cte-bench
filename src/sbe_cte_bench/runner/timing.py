"""Statistical reduction of per-iteration timings.

Per ``docs/01-methodology.md``:

- Report median, p95, p99, min, max, IQR, CV, n.
- **Do not report the mean.** A single GC pause or checkpoint inflates the
  mean while leaving the median stable.
- Flag runs with ``CV > 0.10`` for re-execution.
- At ``n < 100``, p99 is reported but flagged as low-confidence.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

CV_NOISY_THRESHOLD = 0.10
LOW_CONFIDENCE_P99_N_THRESHOLD = 100


@dataclass(frozen=True)
class TimingDistribution:
    """Distribution summary for a set of iteration timings (in milliseconds)."""

    n: int
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    iqr_ms: float
    cv: float
    p99_low_confidence: bool

    def to_dict(self) -> dict[str, Any]:
        # Deliberately omit `mean_ms`. See module docstring.
        return {
            "n": self.n,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "iqr_ms": self.iqr_ms,
            "cv": self.cv,
            "p99_low_confidence": self.p99_low_confidence,
        }


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile.

    Args:
        values: List of numeric values (need not be sorted).
        q: Percentile in ``[0, 100]``.
    """
    if not values:
        raise ValueError("cannot compute percentile of empty list")
    if not 0 <= q <= 100:
        raise ValueError(f"q must be in [0, 100], got {q}")
    sorted_values = sorted(values)
    if q == 0:
        return sorted_values[0]
    if q == 100:
        return sorted_values[-1]
    # Linear interpolation per the spec's "n=20 -> p95 = 19th-of-20" rule.
    rank = (q / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    weight = rank - lo
    return sorted_values[lo] + weight * (sorted_values[hi] - sorted_values[lo])


def summarize(values: list[float]) -> TimingDistribution:
    """Reduce a timing list to a :class:`TimingDistribution` summary."""
    if not values:
        raise ValueError("cannot summarize empty timings")
    n = len(values)
    p50 = percentile(values, 50)
    p25 = percentile(values, 25)
    p75 = percentile(values, 75)
    p95 = percentile(values, 95)
    p99 = percentile(values, 99)
    iqr = p75 - p25

    if n >= 2:
        stddev = statistics.stdev(values)
        mean = statistics.fmean(values)
        cv = stddev / mean if mean else 0.0
    else:
        cv = 0.0

    return TimingDistribution(
        n=n,
        median_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        min_ms=min(values),
        max_ms=max(values),
        iqr_ms=iqr,
        cv=cv,
        p99_low_confidence=n < LOW_CONFIDENCE_P99_N_THRESHOLD,
    )


def is_noisy_run(summary: TimingDistribution) -> bool:
    """Flag a run for re-execution if its CV exceeds the methodology threshold."""
    return summary.cv > CV_NOISY_THRESHOLD
