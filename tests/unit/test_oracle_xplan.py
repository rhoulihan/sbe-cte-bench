"""Tests for the Oracle dbms_xplan parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbe_cte_bench.observability.oracle_xplan import (
    XplanSummary,
    parse_xplan,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "xplan"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text()


@pytest.mark.unit
def test_parse_extracts_plan_hash_value() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    assert isinstance(summary, XplanSummary)
    assert summary.plan_hash == 3947211042


@pytest.mark.unit
def test_parse_extracts_sql_id() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    assert summary.sql_id == "bdpsy0qwd9zxj"


@pytest.mark.unit
def test_parse_inlined_ctes_has_no_temp_table_transformation() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    assert summary.has_materialized_ctes is False


@pytest.mark.unit
def test_parse_materialized_cte_detected() -> None:
    summary = parse_xplan(_load("materialized_cte.txt"))
    assert summary.has_materialized_ctes is True


@pytest.mark.unit
def test_parse_extracts_operations() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    op_names = [op.operation for op in summary.operations]
    assert "SELECT STATEMENT" in op_names
    assert "HASH JOIN" in op_names
    assert "INDEX RANGE SCAN" in op_names


@pytest.mark.unit
def test_parse_extracts_index_used() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    indexes = [op.name for op in summary.operations if "INDEX" in op.operation]
    assert "IX_ORD_DATE" in indexes


@pytest.mark.unit
def test_parse_extracts_cardinality() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    # SORT ORDER BY STOPKEY estimates 100 rows for the FETCH FIRST 100
    sort = next(op for op in summary.operations if "SORT" in op.operation)
    assert sort.rows == 100


@pytest.mark.unit
def test_parse_handles_minimal_input() -> None:
    """Parser is forgiving on malformed/minimal input."""
    summary = parse_xplan("")
    assert summary.plan_hash is None
    assert summary.operations == []


@pytest.mark.unit
def test_parse_serializable_to_dict() -> None:
    summary = parse_xplan(_load("inlined_ctes.txt"))
    d = summary.to_dict()
    assert d["plan_hash"] == 3947211042
    assert "operations" in d
