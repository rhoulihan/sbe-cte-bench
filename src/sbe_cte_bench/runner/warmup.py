"""Warmup management.

Per ``docs/01-methodology.md``:

- 3 warmup iterations precede measurement.
- Warmup timings are *kept* (under ``warmup_ms``) but never folded into the
  measurement set.
- If the gap between warmup-3 and measure-1 is greater than 2x warmup-3, the
  run is invalid (typically a checkpoint storm or log rotation).
"""

from __future__ import annotations

from dataclasses import dataclass

WARMUP_INSTABILITY_FACTOR = 2.0


@dataclass(frozen=True)
class WarmupSplit:
    """Split of an iteration list into warmup and measurement halves."""

    warmup: list[float]
    measured: list[float]
    is_invalid: bool

    @classmethod
    def from_iterations(cls, timings: list[float], *, warmup_count: int) -> WarmupSplit:
        warmup = timings[:warmup_count]
        measured = timings[warmup_count:]

        invalid = False
        if warmup and measured:
            last_warmup = warmup[-1]
            first_measure = measured[0]
            if last_warmup > 0 and first_measure > WARMUP_INSTABILITY_FACTOR * last_warmup:
                invalid = True

        return cls(warmup=warmup, measured=measured, is_invalid=invalid)


def discard_warmups(timings: list[float], *, warmup_count: int) -> list[float]:
    """Return only the measurement-iteration timings (after the warmup prefix)."""
    return timings[warmup_count:]
