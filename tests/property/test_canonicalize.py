"""Property-based tests for the canonicalize function.

The canonicalizer is the load-bearing component of equivalence checking. If it
is wrong, the entire benchmark publishes wrong numbers. These tests verify the
algebraic properties that make canonicalization meaningful:

- Idempotency: canonicalize(canonicalize(x)) == canonicalize(x)
- Order-invariance for dict keys: shuffling keys produces the same output
- Order-invariance for set-valued arrays: shuffling elements produces the same
  output when the path is declared as set-valued
- Float tolerance: values within 1e-9 relative tolerance hash equal
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sbe_cte_bench.equivalence.canonicalize import canonicalize, canonicalize_row


def _json_leaves() -> st.SearchStrategy[Any]:
    """Leaves of a recursively generated JSON-like structure.

    NaN and infinity are excluded - they have no meaningful equivalence
    semantics and would make idempotency tests vacuous.
    """
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**53), max_value=2**53),
        st.floats(allow_nan=False, allow_infinity=False, width=64),
        st.text(alphabet=st.characters(exclude_categories=["Cs"])),
        st.datetimes(timezones=st.just(UTC)),
        st.decimals(allow_nan=False, allow_infinity=False, places=4),
    )


def _json_values() -> st.SearchStrategy[Any]:
    """Recursive nested-JSON-like values."""
    return st.recursive(
        _json_leaves(),
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(
                keys=st.text(min_size=1, max_size=10).filter(lambda s: s.strip()),
                values=children,
                max_size=5,
            ),
        ),
        max_leaves=20,
    )


@pytest.mark.property
@given(value=_json_values())
def test_canonicalize_is_idempotent(value: Any) -> None:
    """canonicalize(canonicalize(x)) == canonicalize(x)."""
    once = canonicalize(value)
    twice = canonicalize(once)
    assert once == twice


@pytest.mark.property
@given(d=st.dictionaries(st.text(min_size=1, max_size=10), _json_leaves(), min_size=2, max_size=10))
def test_canonicalize_dict_key_order_invariant(d: dict[str, Any]) -> None:
    """Shuffling dict keys produces the same canonical form."""
    keys = list(d.keys())
    shuffled = {k: d[k] for k in reversed(keys)}
    assert canonicalize(d) == canonicalize(shuffled)


@pytest.mark.property
@given(rows=st.lists(st.dictionaries(st.text(min_size=1, max_size=5), _json_leaves(), max_size=5)))
def test_canonicalize_row_handles_arbitrary_input(rows: list[dict[str, Any]]) -> None:
    """canonicalize_row never raises on hypothesis-generated input."""
    for row in rows:
        canonicalize_row(row)


@pytest.mark.unit
def test_canonicalize_dict_keys_sorted_alphabetically() -> None:
    """Dict keys must come out in alphabetical order."""
    result = canonicalize({"z": 1, "a": 2, "m": 3})
    assert list(result.keys()) == ["a", "m", "z"]


@pytest.mark.unit
def test_canonicalize_nested_dict_keys_sorted() -> None:
    result = canonicalize({"outer": {"z": 1, "a": 2}})
    assert list(result["outer"].keys()) == ["a", "z"]


@pytest.mark.unit
def test_canonicalize_decimal_to_string_preserves_value() -> None:
    """Decimals canonicalize to a string representation that preserves value."""
    result = canonicalize(Decimal("1.234567890123"))
    assert result == "1.234567890123"


@pytest.mark.unit
def test_canonicalize_float_normalized_to_relative_tolerance() -> None:
    """Floats within 1e-9 relative tolerance canonicalize to the same value."""
    a = canonicalize(1.000000000001)
    b = canonicalize(1.0000000000005)
    assert a == b, f"floats within 1e-9 should canonicalize equal: {a!r} vs {b!r}"


@pytest.mark.unit
def test_canonicalize_float_outside_tolerance_distinct() -> None:
    """Floats outside the relative tolerance canonicalize to distinct values."""
    a = canonicalize(1.0001)
    b = canonicalize(1.0002)
    assert a != b


@pytest.mark.unit
def test_canonicalize_datetime_to_iso8601_utc() -> None:
    dt = datetime(2026, 4, 25, 15, 30, 45, tzinfo=UTC)
    result = canonicalize(dt)
    assert result == "2026-04-25T15:30:45+00:00"


@pytest.mark.unit
def test_canonicalize_naive_datetime_assumed_utc() -> None:
    """Naive datetimes from drivers are conservatively treated as UTC."""
    dt = datetime(2026, 4, 25, 15, 30, 45)
    result = canonicalize(dt)
    assert result == "2026-04-25T15:30:45+00:00"


@pytest.mark.unit
def test_canonicalize_set_valued_array_sorted() -> None:
    """Arrays declared as set-valued have their elements canonicalized then sorted."""
    result = canonicalize_row({"customers": [3, 1, 2]}, set_valued_paths={"customers"})
    assert result["customers"] == [1, 2, 3]


@pytest.mark.unit
def test_canonicalize_non_set_array_preserves_order() -> None:
    """Arrays not declared as set-valued preserve their original order."""
    result = canonicalize_row({"line_items": [3, 1, 2]}, set_valued_paths=set())
    assert result["line_items"] == [3, 1, 2]


@pytest.mark.unit
def test_canonicalize_set_array_with_objects_sorted_by_canonical_form() -> None:
    """Set-valued arrays of objects sort by the canonical string form of each element."""
    result = canonicalize_row(
        {"items": [{"id": 3, "x": 1}, {"id": 1, "x": 2}, {"id": 2, "x": 3}]},
        set_valued_paths={"items"},
    )
    ids = [item["id"] for item in result["items"]]
    assert ids == [1, 2, 3], f"expected sort by id, got {ids}"


@pytest.mark.unit
def test_canonicalize_nan_raises() -> None:
    """NaN cannot canonicalize meaningfully because NaN != NaN."""
    with pytest.raises(ValueError, match="NaN"):
        canonicalize(math.nan)


@pytest.mark.unit
def test_canonicalize_objectid_to_string() -> None:
    """bson.ObjectId canonicalizes to its hex string. Test via duck-typing.

    Avoids importing bson at test time to keep the test pure-unit.
    """

    class FakeObjectId:
        """Minimal duck-type matching bson.ObjectId interface."""

        def __init__(self, hex_str: str) -> None:
            self._hex = hex_str

        def __str__(self) -> str:
            return self._hex

        @property
        def binary(self) -> bytes:
            return bytes.fromhex(self._hex)

    result = canonicalize(FakeObjectId("507f1f77bcf86cd799439011"))
    assert result == "507f1f77bcf86cd799439011"
