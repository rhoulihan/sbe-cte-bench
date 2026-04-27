"""Tests for the equivalence verification pipeline."""

from __future__ import annotations

import pytest

from sbe_cte_bench.equivalence.verify import (
    EquivalenceFailure,
    EquivalenceResult,
    verify_equivalence,
)


@pytest.mark.unit
def test_identical_result_sets_match() -> None:
    rows = [{"a": 1}, {"a": 2}]
    result = verify_equivalence(rows, rows)
    assert isinstance(result, EquivalenceResult)
    assert result.match is True
    assert result.mongo_hash == result.oracle_hash


@pytest.mark.unit
def test_canonically_equal_result_sets_match() -> None:
    """Different dict-key order, same data → match."""
    a = [{"a": 1, "b": 2}]
    b = [{"b": 2, "a": 1}]
    assert verify_equivalence(a, b).match


@pytest.mark.unit
def test_distinct_result_sets_dont_match() -> None:
    a = [{"x": 1}]
    b = [{"x": 2}]
    result = verify_equivalence(a, b)
    assert not result.match
    assert result.mongo_hash != result.oracle_hash


@pytest.mark.unit
def test_different_row_counts_produce_failure() -> None:
    a = [{"x": 1}]
    b = [{"x": 1}, {"x": 1}]
    result = verify_equivalence(a, b)
    assert not result.match
    assert result.row_count_mongo == 1
    assert result.row_count_oracle == 2


@pytest.mark.unit
def test_failure_includes_first_divergence_pointer() -> None:
    a = [{"x": 1}, {"x": 2}, {"x": 3}]
    b = [{"x": 1}, {"x": 2}, {"x": 99}]
    result = verify_equivalence(a, b, sort_rows=False)
    assert not result.match
    assert result.first_divergence is not None
    assert result.first_divergence.row_index == 2
    assert result.first_divergence.field == "x"


@pytest.mark.unit
def test_failure_with_no_divergence_when_only_count_differs() -> None:
    """If counts differ but the common prefix matches, divergence flags the
    extra-rows case rather than a field mismatch."""
    a = [{"x": 1}]
    b = [{"x": 1}, {"x": 2}]
    result = verify_equivalence(a, b, sort_rows=False)
    assert not result.match
    assert result.first_divergence is not None
    assert result.first_divergence.row_index == 1
    assert result.first_divergence.field is None  # extra row, not a field mismatch


@pytest.mark.unit
def test_set_valued_paths_passed_through() -> None:
    """When the caller declares a path as set-valued, equivalence respects that."""
    a = [{"customers": [3, 1, 2]}]
    b = [{"customers": [1, 2, 3]}]
    result = verify_equivalence(a, b, set_valued_paths={"customers"})
    assert result.match


@pytest.mark.unit
def test_assert_equivalent_raises_on_mismatch() -> None:
    """The assert helper raises EquivalenceFailure when results diverge."""
    from sbe_cte_bench.equivalence.verify import assert_equivalent

    with pytest.raises(EquivalenceFailure):
        assert_equivalent([{"x": 1}], [{"x": 2}])


@pytest.mark.unit
def test_assert_equivalent_returns_silently_on_match() -> None:
    from sbe_cte_bench.equivalence.verify import assert_equivalent

    assert_equivalent([{"x": 1}], [{"x": 1}])  # no exception


@pytest.mark.unit
def test_failure_message_mentions_row_and_field() -> None:
    a = [{"x": 1, "y": 2}]
    b = [{"x": 1, "y": 99}]
    result = verify_equivalence(a, b, sort_rows=False)
    msg = result.format_diff()
    assert "row 0" in msg or "row_index=0" in msg
    assert "y" in msg
