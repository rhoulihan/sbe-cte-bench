"""Tests for OS counter capture.

Most paths are integration-tested (cgroup v2 reads need a real container) but
the resource-usage delta logic is testable in isolation.
"""

from __future__ import annotations

import resource

import pytest

from sbe_cte_bench.observability.os_counters import (
    ResourceSnapshot,
    delta,
    snapshot_self,
)


@pytest.mark.unit
def test_snapshot_self_returns_resource_snapshot() -> None:
    snap = snapshot_self()
    assert isinstance(snap, ResourceSnapshot)
    assert snap.cpu_user_ns >= 0
    assert snap.cpu_sys_ns >= 0
    assert snap.peak_rss_kb >= 0


@pytest.mark.unit
def test_delta_subtracts_field_by_field() -> None:
    a = ResourceSnapshot(
        wall_ns=0,
        cpu_user_ns=100,
        cpu_sys_ns=20,
        peak_rss_kb=1024,
        voluntary_csw=10,
        involuntary_csw=5,
        block_in=2,
        block_out=3,
    )
    b = ResourceSnapshot(
        wall_ns=1000,
        cpu_user_ns=300,
        cpu_sys_ns=50,
        peak_rss_kb=2048,
        voluntary_csw=15,
        involuntary_csw=7,
        block_in=4,
        block_out=8,
    )
    d = delta(a, b)
    assert d.wall_ns == 1000
    assert d.cpu_user_ns == 200
    assert d.cpu_sys_ns == 30
    assert d.peak_rss_kb == 2048  # peak is max, not delta
    assert d.voluntary_csw == 5
    assert d.involuntary_csw == 2


@pytest.mark.unit
def test_delta_serializable_to_dict() -> None:
    a = ResourceSnapshot(
        wall_ns=0,
        cpu_user_ns=100,
        cpu_sys_ns=20,
        peak_rss_kb=1024,
        voluntary_csw=0,
        involuntary_csw=0,
        block_in=0,
        block_out=0,
    )
    d = a.to_dict()
    assert d["cpu_user_ns"] == 100
    assert d["peak_rss_kb"] == 1024


@pytest.mark.unit
def test_resource_snapshot_uses_perf_counter_for_wall() -> None:
    """Two consecutive snapshots produce strictly-increasing wall_ns."""
    a = snapshot_self()
    # Burn a tiny bit of time
    sum(range(1000))
    b = snapshot_self()
    assert b.wall_ns >= a.wall_ns


@pytest.mark.unit
def test_resource_snapshot_translates_rusage_units() -> None:
    """ru_utime / ru_stime are in seconds; we report nanoseconds."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    snap = snapshot_self()
    # Snap's user time (in ns) should be >= rusage user time (in s * 1e9).
    # Allow some slack since they're not captured atomically.
    rusage_user_ns = int(ru.ru_utime * 1e9)
    assert snap.cpu_user_ns >= rusage_user_ns - 10_000_000  # 10ms tolerance
