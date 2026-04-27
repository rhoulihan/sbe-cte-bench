"""Statspack snapshot capture and report parsing.

Oracle Database Free Edition does not include AWR (a Diagnostic Pack
feature). Statspack — Oracle's free, included-since-8i performance repository
— provides equivalent system-wide snapshot reports. This module:

1. Wraps SQL*Plus invocation to take a snapshot (``STATSPACK.SNAP``) and
   to generate a diff report (``spreport.sql``).
2. Parses the report's text output into structured fields for inclusion in
   the run record.

The parser is intentionally forgiving — Statspack's report format has minor
variations across patch versions and locales. Sections we don't recognize
are skipped, not errored on.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WaitEvent:
    event: str
    waits: int
    time_seconds: float
    pct_db_time: float


@dataclass(frozen=True)
class StatspackReport:
    begin_snap_id: int | None = None
    end_snap_id: int | None = None
    elapsed_minutes: float | None = None
    db_time_minutes: float | None = None
    top_wait_events: list[WaitEvent] = field(default_factory=list)
    load_profile: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "begin_snap_id": self.begin_snap_id,
            "end_snap_id": self.end_snap_id,
            "elapsed_minutes": self.elapsed_minutes,
            "db_time_minutes": self.db_time_minutes,
            "top_wait_events": [
                {
                    "event": e.event,
                    "waits": e.waits,
                    "time_seconds": e.time_seconds,
                    "pct_db_time": e.pct_db_time,
                }
                for e in self.top_wait_events
            ],
            "load_profile": self.load_profile,
        }


_SNAP_RE = re.compile(
    r"Begin Snap:\s+(\d+).*?End Snap:\s+(\d+)\s.*?Elapsed:\s+([\d.]+).*?DB time:\s+([\d.]+)",
    re.DOTALL,
)

# Top wait events table — match lines like
#   db file sequential read     18,420       2.5         46.1   45.2
# The event name can contain spaces; we anchor on the trailing 4 numerics.
_WAIT_EVENT_RE = re.compile(
    r"^([A-Za-z][\w :/.()-]*?)\s{2,}"
    r"([\d,]+)?\s+"  # waits (optional for events like DB CPU)
    r"([\d.]+)?\s+"  # avg ms (optional)
    r"([\d,.]+)\s+"  # time (s)
    r"([\d.]+)\s*$",  # pct db time
    re.MULTILINE,
)

_LOAD_PROFILE_PATTERNS = {
    "logical_reads_per_s": r"Logical reads:\s+([\d,.]+)",
    "physical_reads_per_s": r"Physical reads:\s+([\d,.]+)",
    "parses_per_s": r"Parses:\s+([\d,.]+)",
    "hard_parses_per_s": r"Hard parses:\s+([\d,.]+)",
    "executes_per_s": r"Executes:\s+([\d,.]+)",
    "redo_per_s": r"Redo size \(bytes\):\s+([\d,.]+)",
}


def parse_statspack_report(text: str) -> StatspackReport:
    """Parse a Statspack ``spreport.sql`` text output into a structured form."""
    if not text:
        return StatspackReport()

    snap_match = _SNAP_RE.search(text)
    begin_snap = end_snap = None
    elapsed = db_time = None
    if snap_match:
        begin_snap = int(snap_match.group(1))
        end_snap = int(snap_match.group(2))
        elapsed = float(snap_match.group(3))
        db_time = float(snap_match.group(4))

    load_profile: dict[str, float] = {}
    for key, pattern in _LOAD_PROFILE_PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            load_profile[key] = _parse_number(m.group(1))

    top_events = _parse_top_wait_events(text)

    return StatspackReport(
        begin_snap_id=begin_snap,
        end_snap_id=end_snap,
        elapsed_minutes=elapsed,
        db_time_minutes=db_time,
        top_wait_events=top_events,
        load_profile=load_profile,
    )


def _parse_top_wait_events(text: str) -> list[WaitEvent]:
    """Extract the Top 5 Timed Events table.

    Locates the section and parses subsequent table rows until a blank or
    separator line.
    """
    section_start = text.find("Top 5 Timed Events")
    if section_start == -1:
        return []
    # Skip to table contents; the header has a separator line.
    rest = text[section_start:]
    events: list[WaitEvent] = []
    for line in rest.splitlines():
        if not line.strip() or line.lstrip().startswith("-"):
            if events:
                break
            continue
        if line.lstrip().startswith(("Event", "Top 5", "~")):
            continue
        match = _WAIT_EVENT_RE.match(line)
        if match:
            event = match.group(1).strip()
            if event in ("Event",):
                continue
            waits = _parse_int(match.group(2)) if match.group(2) else 0
            time_s = _parse_number(match.group(4))
            pct = _parse_number(match.group(5))
            events.append(
                WaitEvent(
                    event=event,
                    waits=waits,
                    time_seconds=time_s,
                    pct_db_time=pct,
                )
            )
            if len(events) >= 5:
                break
    return events


def _parse_number(value: str) -> float:
    return float(value.replace(",", ""))


def _parse_int(value: str) -> int:
    return int(value.replace(",", ""))


# ─── SQL*Plus invocation ──────────────────────────────────────────────────


def take_snapshot(
    *, dsn: str, perfstat_password: str, snap_level: int = 7
) -> int:  # pragma: no cover - integration only
    """Take a Statspack snapshot via SQL*Plus and return the new SNAP_ID.

    Not exercised by unit tests (requires a live Oracle database). Integration
    tests call this with a testcontainers-managed Oracle Free instance.
    """
    script = f"""
    set heading off feedback off pages 0 echo off
    variable s number
    begin :s := statspack.snap(i_snap_level => {int(snap_level)}); end;
    /
    print s
    exit
    """
    proc = subprocess.run(
        ["sqlplus", "-S", "-L", f"perfstat/{perfstat_password}@{dsn}"],  # noqa: S607
        input=script,
        capture_output=True,
        text=True,
        check=True,
    )
    output = proc.stdout.strip()
    digits = re.search(r"(\d+)", output)
    if not digits:
        raise RuntimeError(f"could not parse snap_id from sqlplus output: {output!r}")
    return int(digits[1])


def generate_report(
    *,
    dsn: str,
    perfstat_password: str,
    begin_snap: int,
    end_snap: int,
    output_path: Path,
) -> StatspackReport:  # pragma: no cover - integration only
    """Run ``spreport.sql`` and parse its output.

    The three ``define`` lines suppress the prompts that ``spreport.sql``
    otherwise issues, per the canonical command-line invocation pattern.
    """
    script = f"""
    set heading off feedback off pages 5000 lines 200 echo off
    define begin_snap={begin_snap}
    define end_snap={end_snap}
    define report_name={output_path}
    @?/rdbms/admin/spreport.sql
    exit
    """
    subprocess.run(
        ["sqlplus", "-S", "-L", f"perfstat/{perfstat_password}@{dsn}"],  # noqa: S607
        input=script,
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_statspack_report(output_path.read_text())
