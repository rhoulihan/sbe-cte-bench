"""Tests for the runner orchestration logic.

The orchestration pieces under test are pure-logic: warmup discard semantics,
alternating-system iteration order, per-iteration timeout enforcement. The
parts that touch real engines belong in integration tests; here we validate
the algorithm.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sbe_cte_bench.runner.alternating import iteration_order, run_alternating
from sbe_cte_bench.runner.warmup import discard_warmups


@pytest.mark.unit
def test_iteration_order_alternates_systems() -> None:
    """For n=4, the order is mongo, oracle, mongo, oracle, ...."""
    order = list(iteration_order(n=4, systems=("mongo", "oracle")))
    assert order == ["mongo", "oracle", "mongo", "oracle", "mongo", "oracle", "mongo", "oracle"]


@pytest.mark.unit
def test_iteration_order_emits_n_iterations_per_system() -> None:
    order = iteration_order(n=20, systems=("mongo", "oracle"))
    counts = {"mongo": 0, "oracle": 0}
    for system in order:
        counts[system] += 1
    assert counts == {"mongo": 20, "oracle": 20}


@pytest.mark.unit
def test_iteration_order_with_warmup_prefix() -> None:
    """Warmup iterations come before measurement iterations and alternate too."""
    order = list(iteration_order(n=2, systems=("mongo", "oracle"), warmup=3))
    # 3 warmups per system, then 2 measurements per system, alternating
    expected = [
        "mongo",
        "oracle",  # warmup-1
        "mongo",
        "oracle",  # warmup-2
        "mongo",
        "oracle",  # warmup-3
        "mongo",
        "oracle",  # measure-1
        "mongo",
        "oracle",  # measure-2
    ]
    assert order == expected


@pytest.mark.unit
def test_discard_warmups_removes_first_k() -> None:
    timings = [100.0, 95.0, 90.0, 50.0, 51.0, 52.0]
    measured = discard_warmups(timings, warmup_count=3)
    assert measured == [50.0, 51.0, 52.0]


@pytest.mark.unit
def test_discard_warmups_zero_warmup() -> None:
    timings = [50.0, 51.0]
    assert discard_warmups(timings, warmup_count=0) == [50.0, 51.0]


@pytest.mark.unit
def test_discard_warmups_preserves_warmup_in_separate_field() -> None:
    """Discarded warmups are returned for diagnostic reporting."""
    from sbe_cte_bench.runner.warmup import WarmupSplit

    timings = [100.0, 95.0, 90.0, 50.0, 51.0, 52.0]
    split = WarmupSplit.from_iterations(timings, warmup_count=3)
    assert split.warmup == [100.0, 95.0, 90.0]
    assert split.measured == [50.0, 51.0, 52.0]


@pytest.mark.unit
def test_warmup_split_rejects_first_iteration_too_slow() -> None:
    """Per methodology: if measure-1 is >2x warmup-3, the run is invalid."""
    from sbe_cte_bench.runner.warmup import WarmupSplit

    timings = [100.0, 95.0, 90.0, 250.0, 51.0, 52.0]  # measure-1 (250) > 2x warmup-3 (90)
    split = WarmupSplit.from_iterations(timings, warmup_count=3)
    assert split.is_invalid is True


@pytest.mark.unit
def test_warmup_split_accepts_normal_progression() -> None:
    from sbe_cte_bench.runner.warmup import WarmupSplit

    timings = [100.0, 95.0, 90.0, 50.0, 51.0, 52.0]
    split = WarmupSplit.from_iterations(timings, warmup_count=3)
    assert split.is_invalid is False


@pytest.mark.unit
def test_run_alternating_collects_per_system_timings() -> None:
    """run_alternating returns a dict mapping system → timings."""
    counts = {"mongo": 0, "oracle": 0}

    def make_runner(system: str) -> Callable[[], float]:
        def run() -> float:
            counts[system] += 1
            return float(counts[system] * 10)

        return run

    runners = {
        "mongo": make_runner("mongo"),
        "oracle": make_runner("oracle"),
    }
    timings = run_alternating(runners=runners, n=3, warmup=0)
    assert timings["mongo"] == [10.0, 20.0, 30.0]
    assert timings["oracle"] == [10.0, 20.0, 30.0]


@pytest.mark.unit
def test_run_alternating_calls_warmup_first() -> None:
    """Warmup invocations precede measurement, in alternating order."""
    call_log: list[str] = []

    def runner(system: str) -> Callable[[], float]:
        def run() -> float:
            call_log.append(system)
            return 0.0

        return run

    runners = {
        "mongo": runner("mongo"),
        "oracle": runner("oracle"),
    }
    run_alternating(runners=runners, n=2, warmup=1)
    # 1 warmup + 2 measurement per system, all alternating
    assert call_log == ["mongo", "oracle", "mongo", "oracle", "mongo", "oracle"]
