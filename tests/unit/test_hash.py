"""Tests for the result-set hashing module."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sbe_cte_bench.equivalence.hash import hash_result_set


@pytest.mark.unit
def test_hash_is_deterministic() -> None:
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert hash_result_set(rows) == hash_result_set(rows)


@pytest.mark.unit
def test_hash_invariant_to_dict_key_order() -> None:
    a = [{"a": 1, "b": 2}]
    b = [{"b": 2, "a": 1}]
    assert hash_result_set(a) == hash_result_set(b)


@pytest.mark.unit
def test_hash_distinguishes_different_values() -> None:
    a = [{"x": 1}]
    b = [{"x": 2}]
    assert hash_result_set(a) != hash_result_set(b)


@pytest.mark.unit
def test_hash_distinguishes_different_row_counts() -> None:
    a = [{"x": 1}]
    b = [{"x": 1}, {"x": 1}]
    assert hash_result_set(a) != hash_result_set(b)


@pytest.mark.unit
def test_hash_collapses_floats_within_tolerance() -> None:
    """Floats within 1e-9 relative tolerance hash equal."""
    a = [{"v": 1.000000000001}]
    b = [{"v": 1.0000000000005}]
    assert hash_result_set(a) == hash_result_set(b)


@pytest.mark.unit
def test_hash_distinguishes_floats_outside_tolerance() -> None:
    a = [{"v": 1.0001}]
    b = [{"v": 1.0002}]
    assert hash_result_set(a) != hash_result_set(b)


@pytest.mark.unit
def test_hash_normalizes_decimal_to_string() -> None:
    """Decimal('1.5') and float 1.5 both stringify to compatible forms."""
    a = [{"v": Decimal("1.5")}]
    b = [{"v": Decimal("1.50")}]  # same value, different exponent
    assert hash_result_set(a) == hash_result_set(b)


@pytest.mark.unit
def test_hash_handles_datetime_with_or_without_tz() -> None:
    """Naive datetime is treated as UTC; tz-aware datetime in UTC matches."""
    naive = datetime(2026, 4, 25, 12, 0, 0)
    aware = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    assert hash_result_set([{"t": naive}]) == hash_result_set([{"t": aware}])


@pytest.mark.unit
def test_hash_returns_sha256_hex_string() -> None:
    result = hash_result_set([{"x": 1}])
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


@pytest.mark.unit
def test_hash_uses_set_valued_paths() -> None:
    """Set-valued paths hash equal regardless of element order."""
    a = [{"customers": [3, 1, 2]}]
    b = [{"customers": [1, 2, 3]}]
    assert hash_result_set(a, set_valued_paths={"customers"}) == hash_result_set(
        b, set_valued_paths={"customers"}
    )


@pytest.mark.unit
def test_hash_without_set_valued_paths_distinguishes_array_order() -> None:
    a = [{"customers": [3, 1, 2]}]
    b = [{"customers": [1, 2, 3]}]
    assert hash_result_set(a) != hash_result_set(b)


@pytest.mark.unit
def test_hash_distinguishes_row_order_when_unsorted() -> None:
    """Without explicit sort, hash sees row order as significant."""
    a = [{"x": 1}, {"x": 2}]
    b = [{"x": 2}, {"x": 1}]
    assert hash_result_set(a, sort_rows=False) != hash_result_set(b, sort_rows=False)


@pytest.mark.unit
def test_hash_invariant_to_row_order_when_sort_rows_true() -> None:
    """With sort_rows=True (default), row order doesn't matter."""
    a = [{"x": 1}, {"x": 2}]
    b = [{"x": 2}, {"x": 1}]
    assert hash_result_set(a, sort_rows=True) == hash_result_set(b, sort_rows=True)


@pytest.mark.unit
def test_hash_empty_result_set() -> None:
    """Empty result hashes deterministically (and not to the same as a single empty row)."""
    assert hash_result_set([]) == hash_result_set([])
    assert hash_result_set([]) != hash_result_set([{}])
