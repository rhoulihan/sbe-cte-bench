"""Canonicalize result rows from MongoDB and Oracle into a comparable form.

The canonicalizer's job is to take the raw output of a driver — which contains
engine-specific types like ``bson.ObjectId``, ``Decimal128``, naive datetimes
from ``python-oracledb``, and arbitrary key ordering — and reduce it to a
comparable JSON-like structure where two semantically-equivalent results from
different engines hash to the same digest.

Algebraic invariants this module enforces (covered by property-based tests):

- Idempotency: ``canonicalize(canonicalize(x)) == canonicalize(x)``
- Order-invariance for dict keys
- Order-invariance for arrays declared as set-valued
- Float normalization to a fixed relative tolerance (1e-9)

Anything that has no meaningful equivalence (NaN, infinity) is rejected loudly.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

# Relative tolerance for float comparison. Set to 1e-9 per docs/01-methodology.md.
_FLOAT_RELATIVE_TOLERANCE = 1e-9


def canonicalize(value: Any) -> Any:
    """Reduce ``value`` to a canonical form suitable for hashing.

    Mappings → dicts with keys sorted alphabetically.
    Sequences → lists with elements canonicalized (order preserved).
    Floats → rounded to a fixed number of significant digits.
    Decimals → str.
    Datetimes → ISO 8601 string in UTC.
    bson.ObjectId / similar duck-types with ``__str__`` returning a hex → str.
    None / bool / int / str → unchanged.
    NaN, infinity → ValueError.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # Normalize int -> float so cross-engine comparisons treat 455 and
        # 455.0 as equal. Drivers vary: pymongo returns float for $sum even
        # when the result is whole; python-oracledb returns int for NUMBER
        # with no fractional part. Both must canonicalize identically.
        return _normalize_float(float(value))
    if isinstance(value, float):
        if math.isnan(value):
            raise ValueError(f"cannot canonicalize NaN: {value!r}")
        if math.isinf(value):
            raise ValueError(f"cannot canonicalize infinity: {value!r}")
        return _normalize_float(value)
    if isinstance(value, Decimal):
        return _decimal_to_str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return _datetime_to_iso(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {k: canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [canonicalize(v) for v in value]
    # Duck-typed handling for bson.ObjectId / UUID-like / other engine-specific
    # types that stringify to a stable representation.
    str_form = str(value)
    return str_form


def canonicalize_row(
    row: dict[str, Any],
    set_valued_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Canonicalize a single result row.

    ``set_valued_paths`` is the set of top-level field names whose array values
    should be sorted (because the engine's emitting them as a set, not a list).
    Common examples: ``$addToSet`` outputs from MongoDB, ``COLLECT DISTINCT``
    on Oracle.

    The current implementation only supports top-level paths (no dotted paths
    into nested objects). Nested set-valued arrays are uncommon in this
    benchmark; if a scenario needs them, extend here.
    """
    set_paths = set_valued_paths or set()
    canonical: dict[str, Any] = {}
    for key in sorted(row.keys()):
        canonical_value = canonicalize(row[key])
        if key in set_paths and isinstance(canonical_value, list):
            canonical_value = sorted(canonical_value, key=_sort_key)
        canonical[key] = canonical_value
    return canonical


def _normalize_float(value: float) -> float:
    """Round a float so values within ``_FLOAT_RELATIVE_TOLERANCE`` collapse.

    Using ``math.isclose`` for comparison would not give us a deterministic
    canonical form. Instead we round to a fixed number of significant digits
    derived from the tolerance. With ``rel_tol=1e-9``, that's 10 sig figs.

    Implementation note: ``round(value, decimals)`` overflows for extreme
    values (e.g. ``decimals=-299`` for ``1.79e308``). Format-then-parse via
    ``%g`` is overflow-safe and gives the same significant-digits rounding.
    """
    if value == 0.0:
        return 0.0
    sig_figs = -math.floor(math.log10(_FLOAT_RELATIVE_TOLERANCE)) + 1
    rounded = float(f"{value:.{sig_figs}g}")
    # At the extremes of float range, rounding the last sig-fig up can
    # overflow to inf. Fall back to the original value when that happens —
    # idempotency holds because subsequent calls will see the original.
    if math.isinf(rounded):
        return value
    return rounded


def _decimal_to_str(value: Decimal) -> str:
    """Decimal to a normalized string form. Strips trailing zeros."""
    normalized = value.normalize()
    return format(normalized, "f")


def _datetime_to_iso(value: datetime) -> str:
    """Datetime to ISO 8601 in UTC.

    Naive datetimes are conservatively treated as UTC. The benchmark workload
    inserts dates in UTC; drivers sometimes return naive datetimes (especially
    Oracle's ``DATE`` type via python-oracledb thin mode), and treating them as
    UTC produces consistent canonical forms.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat()


def _sort_key(canonical_value: Any) -> str:
    """Sort key for set-valued array elements.

    Elements may be heterogeneous (scalars, dicts). Convert to a JSON-stable
    string for comparison. We don't need a fast sort; we need a deterministic
    one.
    """
    import json

    return json.dumps(canonical_value, sort_keys=True, default=str)
