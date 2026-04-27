"""OS-level resource counters captured around scenario iterations.

The harness brackets each iteration with a :func:`snapshot_self` call before
and after; :func:`delta` reduces the pair to per-iteration deltas. These
counters complement the engine-internal instrumentation (explain, statspack,
spill metrics) by capturing wall clock, CPU consumption, RSS peak, context
switches, and block I/O — diagnostics that don't have engine-side analogues.

Two snapshot scopes:

- :func:`snapshot_self` — counters for the harness process itself.
- :func:`snapshot_container` (integration only) — reads cgroup v2 stats for a
  named container.
"""

from __future__ import annotations

import resource
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResourceSnapshot:
    """Point-in-time resource counter snapshot."""

    wall_ns: int
    cpu_user_ns: int
    cpu_sys_ns: int
    peak_rss_kb: int
    voluntary_csw: int
    involuntary_csw: int
    block_in: int
    block_out: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_ns": self.wall_ns,
            "cpu_user_ns": self.cpu_user_ns,
            "cpu_sys_ns": self.cpu_sys_ns,
            "peak_rss_kb": self.peak_rss_kb,
            "voluntary_csw": self.voluntary_csw,
            "involuntary_csw": self.involuntary_csw,
            "block_in": self.block_in,
            "block_out": self.block_out,
        }


def snapshot_self() -> ResourceSnapshot:
    """Capture a snapshot of the current process's resource counters."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ResourceSnapshot(
        wall_ns=time.perf_counter_ns(),
        cpu_user_ns=int(ru.ru_utime * 1_000_000_000),
        cpu_sys_ns=int(ru.ru_stime * 1_000_000_000),
        peak_rss_kb=ru.ru_maxrss,
        voluntary_csw=ru.ru_nvcsw,
        involuntary_csw=ru.ru_nivcsw,
        block_in=ru.ru_inblock,
        block_out=ru.ru_oublock,
    )


def delta(before: ResourceSnapshot, after: ResourceSnapshot) -> ResourceSnapshot:
    """Compute per-iteration deltas. ``peak_rss_kb`` is the *max* (not delta)."""
    return ResourceSnapshot(
        wall_ns=after.wall_ns - before.wall_ns,
        cpu_user_ns=after.cpu_user_ns - before.cpu_user_ns,
        cpu_sys_ns=after.cpu_sys_ns - before.cpu_sys_ns,
        peak_rss_kb=max(before.peak_rss_kb, after.peak_rss_kb),
        voluntary_csw=after.voluntary_csw - before.voluntary_csw,
        involuntary_csw=after.involuntary_csw - before.involuntary_csw,
        block_in=after.block_in - before.block_in,
        block_out=after.block_out - before.block_out,
    )


# ─── Container-side cgroup v2 reads ──────────────────────────────────────


def read_cgroup_v2(
    container_id: str, *, cgroup_root: Path | str = "/sys/fs/cgroup"
) -> dict[str, int]:  # pragma: no cover - integration only
    """Read the relevant cgroup v2 counters for a container.

    Docker's cgroup path under cgroup v2 is typically
    ``/sys/fs/cgroup/system.slice/docker-<container_id>.scope/``. The exact
    mount may vary by Docker version and host; the harness probes likely
    locations and returns whichever resolves.
    """
    root = Path(cgroup_root)
    candidates = [
        root / f"system.slice/docker-{container_id}.scope",
        root / f"docker/{container_id}",
    ]
    cgroup_dir = next((c for c in candidates if c.exists()), None)
    if cgroup_dir is None:
        raise FileNotFoundError(f"cgroup not found for container {container_id}")

    return {
        "cpu_usage_us": _read_cpu_stat(cgroup_dir),
        "memory_current_bytes": _read_int(cgroup_dir / "memory.current"),
        "memory_peak_bytes": _read_int(cgroup_dir / "memory.peak"),
    }


def _read_cpu_stat(cgroup_dir: Path) -> int:  # pragma: no cover - integration only
    text = (cgroup_dir / "cpu.stat").read_text()
    for line in text.splitlines():
        if line.startswith("usage_usec "):
            return int(line.split()[1])
    return 0


def _read_int(path: Path) -> int:  # pragma: no cover - integration only
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0
