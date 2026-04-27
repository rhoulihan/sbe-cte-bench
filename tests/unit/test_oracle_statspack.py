"""Tests for the Statspack report parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbe_cte_bench.observability.oracle_statspack import (
    StatspackReport,
    parse_statspack_report,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "statspack"


@pytest.mark.unit
def test_parse_report_returns_summary() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    assert isinstance(report, StatspackReport)


@pytest.mark.unit
def test_parse_report_extracts_snap_ids() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    assert report.begin_snap_id == 5
    assert report.end_snap_id == 6


@pytest.mark.unit
def test_parse_report_extracts_elapsed_minutes() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    assert report.elapsed_minutes == pytest.approx(5.08, rel=0.01)


@pytest.mark.unit
def test_parse_report_extracts_top_wait_events() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    events = report.top_wait_events
    assert len(events) >= 3
    names = [e.event for e in events]
    assert "db file sequential read" in names
    assert "DB CPU" in names


@pytest.mark.unit
def test_parse_report_top_event_has_waits() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    events = report.top_wait_events
    seq = next(e for e in events if e.event == "db file sequential read")
    assert seq.waits == 18420
    assert seq.time_seconds == pytest.approx(46.1, rel=0.01)


@pytest.mark.unit
def test_parse_report_extracts_load_profile() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    assert report.load_profile["logical_reads_per_s"] == pytest.approx(4512.5, rel=0.01)
    assert report.load_profile["physical_reads_per_s"] == pytest.approx(120.3, rel=0.01)


@pytest.mark.unit
def test_parse_report_handles_empty_input() -> None:
    report = parse_statspack_report("")
    assert report.begin_snap_id is None
    assert report.top_wait_events == []
    assert report.load_profile == {}


@pytest.mark.unit
def test_parse_report_serializable_to_dict() -> None:
    text = (_FIXTURES / "sample_report.txt").read_text()
    report = parse_statspack_report(text)
    d = report.to_dict()
    assert d["begin_snap_id"] == 5
    assert d["end_snap_id"] == 6
    assert "top_wait_events" in d
