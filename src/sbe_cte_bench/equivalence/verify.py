"""Equivalence verification pipeline.

Top-level entry point: ``verify_equivalence(mongo_rows, oracle_rows, ...)``.
Returns an :class:`EquivalenceResult` carrying the two hashes, the per-engine
row counts, and (on mismatch) a localized first-divergence pointer that names
the row index and the field whose values disagree.

The first-divergence pointer is what makes equivalence failures actionable.
Without it, a non-matching hash means "these two result sets differ
somewhere" — useless for debugging. With it, a failure points the engineer at
the exact row and field that disagrees.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sbe_cte_bench.equivalence.canonicalize import canonicalize_row
from sbe_cte_bench.equivalence.hash import _row_sort_key, hash_result_set


class EquivalenceFailure(AssertionError):  # noqa: N818 — Failure reads more naturally than Error here, and it's an AssertionError subclass
    """Raised by :func:`assert_equivalent` when result sets diverge."""


@dataclass(frozen=True)
class Divergence:
    """First-divergence pointer for a failed equivalence check."""

    row_index: int
    field: str | None  # None when the divergence is "extra row" rather than a field mismatch
    mongo_value: Any
    oracle_value: Any


@dataclass(frozen=True)
class EquivalenceResult:
    """Outcome of an equivalence check."""

    match: bool
    mongo_hash: str
    oracle_hash: str
    row_count_mongo: int
    row_count_oracle: int
    first_divergence: Divergence | None

    def format_diff(self) -> str:
        """Single-screen-readable diff for failed equivalence."""
        if self.match:
            return "match"
        lines = [
            f"hash mismatch: mongo={self.mongo_hash[:12]}... oracle={self.oracle_hash[:12]}...",
            f"row counts: mongo={self.row_count_mongo} oracle={self.row_count_oracle}",
        ]
        if self.first_divergence is not None:
            d = self.first_divergence
            if d.field is None:
                lines.append(f"divergence at row {d.row_index}: extra/missing row")
            else:
                lines.append(
                    f"divergence at row {d.row_index} field {d.field!r}: "
                    f"mongo={d.mongo_value!r} oracle={d.oracle_value!r}"
                )
        return "\n".join(lines)


def verify_equivalence(
    mongo_rows: Iterable[dict[str, Any]],
    oracle_rows: Iterable[dict[str, Any]],
    *,
    set_valued_paths: set[str] | None = None,
    sort_rows: bool = True,
) -> EquivalenceResult:
    """Verify two result sets are equivalent.

    Args:
        mongo_rows: Result rows from the MongoDB side.
        oracle_rows: Result rows from the Oracle side.
        set_valued_paths: Top-level field names treated as set-valued.
        sort_rows: When True (default), row order is normalized before
            comparison. When False, row order matters (used for scenarios with
            explicit ORDER BY).
    """
    mongo_list = list(mongo_rows)
    oracle_list = list(oracle_rows)

    mongo_hash = hash_result_set(mongo_list, set_valued_paths=set_valued_paths, sort_rows=sort_rows)
    oracle_hash = hash_result_set(
        oracle_list, set_valued_paths=set_valued_paths, sort_rows=sort_rows
    )

    if mongo_hash == oracle_hash:
        return EquivalenceResult(
            match=True,
            mongo_hash=mongo_hash,
            oracle_hash=oracle_hash,
            row_count_mongo=len(mongo_list),
            row_count_oracle=len(oracle_list),
            first_divergence=None,
        )

    divergence = _find_first_divergence(
        mongo_list, oracle_list, set_valued_paths=set_valued_paths, sort_rows=sort_rows
    )
    return EquivalenceResult(
        match=False,
        mongo_hash=mongo_hash,
        oracle_hash=oracle_hash,
        row_count_mongo=len(mongo_list),
        row_count_oracle=len(oracle_list),
        first_divergence=divergence,
    )


def assert_equivalent(
    mongo_rows: Iterable[dict[str, Any]],
    oracle_rows: Iterable[dict[str, Any]],
    *,
    set_valued_paths: set[str] | None = None,
    sort_rows: bool = True,
) -> None:
    """Raise :class:`EquivalenceFailure` if the two result sets diverge."""
    result = verify_equivalence(
        mongo_rows,
        oracle_rows,
        set_valued_paths=set_valued_paths,
        sort_rows=sort_rows,
    )
    if not result.match:
        raise EquivalenceFailure(result.format_diff())


def _find_first_divergence(
    mongo_rows: list[dict[str, Any]],
    oracle_rows: list[dict[str, Any]],
    *,
    set_valued_paths: set[str] | None,
    sort_rows: bool,
) -> Divergence | None:
    """Locate the row and field where the two result sets first disagree.

    Walks both sides in canonical-sorted order (when ``sort_rows`` is True) so
    the divergence reflects the canonical form, not driver-incidental ordering.
    """
    canonical_mongo = [canonicalize_row(r, set_valued_paths=set_valued_paths) for r in mongo_rows]
    canonical_oracle = [canonicalize_row(r, set_valued_paths=set_valued_paths) for r in oracle_rows]
    if sort_rows:
        canonical_mongo.sort(key=_row_sort_key)
        canonical_oracle.sort(key=_row_sort_key)

    for i, (m_row, o_row) in enumerate(zip(canonical_mongo, canonical_oracle, strict=False)):
        if m_row == o_row:
            continue
        # Find the first field that differs.
        all_fields = sorted(set(m_row.keys()) | set(o_row.keys()))
        for field in all_fields:
            if m_row.get(field) != o_row.get(field):
                return Divergence(
                    row_index=i,
                    field=field,
                    mongo_value=m_row.get(field),
                    oracle_value=o_row.get(field),
                )
        # Defensive: row dicts differ overall but no specific field flagged.
        # This should not happen given the rows compared unequal above.
        return Divergence(  # pragma: no cover
            row_index=i, field=None, mongo_value=m_row, oracle_value=o_row
        )

    # One side is longer than the other.
    shorter_len = min(len(canonical_mongo), len(canonical_oracle))
    if len(canonical_mongo) != len(canonical_oracle):
        return Divergence(
            row_index=shorter_len,
            field=None,
            mongo_value=canonical_mongo[shorter_len:],
            oracle_value=canonical_oracle[shorter_len:],
        )
    return None
