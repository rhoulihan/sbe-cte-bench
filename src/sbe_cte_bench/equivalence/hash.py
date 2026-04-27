"""SHA-256 hashing of canonicalized result sets.

Two result sets that hash to the same digest are *equivalent* by the
benchmark's equivalence definition: they contain the same rows under
canonical form, with the same multiplicity, and (depending on the
``sort_rows`` flag) either the same or any row order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from sbe_cte_bench.equivalence.canonicalize import canonicalize_row


def hash_result_set(
    rows: Iterable[dict[str, Any]],
    *,
    set_valued_paths: set[str] | None = None,
    sort_rows: bool = True,
) -> str:
    """Hash a result set.

    Args:
        rows: Iterable of row dicts.
        set_valued_paths: Field names whose array values should be treated as
            sets (sorted before hashing). Common case: MongoDB's ``$addToSet``
            and Oracle's ``COLLECT DISTINCT``.
        sort_rows: When True (the default), the rows themselves are sorted by
            their canonical form before hashing — so row order doesn't affect
            the digest. Set False to make order-significant comparisons (used
            in scenarios with explicit ``ORDER BY``).

    Returns:
        SHA-256 hex digest as a 64-character lowercase string.
    """
    canonical_rows = [canonicalize_row(row, set_valued_paths=set_valued_paths) for row in rows]
    if sort_rows:
        canonical_rows.sort(key=_row_sort_key)

    payload = json.dumps(canonical_rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_sort_key(row: dict[str, Any]) -> str:
    """Sort key for canonical rows."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
