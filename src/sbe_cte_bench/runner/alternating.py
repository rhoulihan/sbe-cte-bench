"""Alternating-system iteration order.

Per ``docs/01-methodology.md`` the iteration sequence is system, system,
system... within each scenario:

    warmup-mongo-1, warmup-oracle-1
    warmup-mongo-2, warmup-oracle-2
    warmup-mongo-3, warmup-oracle-3
    mongo-1, oracle-1
    mongo-2, oracle-2
    ...

Alternating prevents systematic bias from background processes that would
otherwise hit one system disproportionately if all of one system's iterations
ran before the other's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator


def iteration_order(
    *,
    n: int,
    systems: tuple[str, ...] = ("mongo", "oracle"),
    warmup: int = 0,
) -> Iterator[str]:
    """Yield the alternating iteration sequence as system labels.

    Args:
        n: Measurement iterations per system.
        systems: Tuple of system labels (default: mongo, oracle).
        warmup: Warmup iterations per system, prefixed before measurement.
    """
    for _ in range(warmup + n):
        yield from systems


def run_alternating(
    *,
    runners: dict[str, Callable[[], float]],
    n: int,
    warmup: int = 0,
) -> dict[str, list[float]]:
    """Drive a set of per-system runners in alternating order.

    Args:
        runners: Mapping of system name → no-arg callable returning a timing in ms.
        n: Measurement iterations per system.
        warmup: Warmup iterations per system; results returned but not part of
            the measurement set.

    Returns:
        Mapping of system name → list of *all* timings (warmup + measured).
        Splitting is the caller's responsibility (use :class:`WarmupSplit`).
    """
    systems = tuple(runners.keys())
    timings: dict[str, list[float]] = {s: [] for s in systems}

    for system in iteration_order(n=n, systems=systems, warmup=warmup):
        runner = runners[system]
        timings[system].append(runner())

    return timings


__all__ = ("iteration_order", "run_alternating")
