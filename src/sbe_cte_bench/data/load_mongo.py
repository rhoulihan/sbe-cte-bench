"""MongoDB loader.

Reads JSONL files produced by ``generator.py`` and inserts them into the
benchmark collections via ``pymongo``'s bulk-write APIs. We don't shell out
to ``mongorestore`` because (a) we already have a connection through pymongo
for the rest of the harness, and (b) bulk inserts of pre-validated JSONL
documents are within ~5% of mongorestore performance and avoid the
external-tool dependency.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymongo import InsertOne

from sbe_cte_bench.drivers.mongo import MongoBench

_ENTITIES = (
    ("regions.jsonl", "regions"),
    ("suppliers.jsonl", "suppliers"),
    ("categories.jsonl", "categories"),
    ("products.jsonl", "products"),
    ("customers.jsonl", "customers"),
    ("orders.jsonl", "orders"),
)


@dataclass(frozen=True)
class LoadStats:
    inserted: int
    elapsed_s: float


def load_mongodb(
    *,
    bench: MongoBench,
    data_dir: Path | str,
    batch_size: int = 1000,
    drop_existing: bool = True,
    create_indexes: bool = True,
) -> dict[str, LoadStats]:
    """Load all benchmark entities from ``data_dir`` into the Mongo bench DB.

    Args:
        bench: Open :class:`MongoBench` connection.
        data_dir: Directory containing ``regions.jsonl``, ``orders.jsonl``, etc.
        batch_size: Rows per ``bulk_write`` call.
        drop_existing: If True, drops each collection before loading.
        create_indexes: If True, creates the parity indexes after load.

    Returns:
        Mapping of collection name -> :class:`LoadStats`.
    """
    import time

    src = Path(data_dir)
    stats: dict[str, LoadStats] = {}
    for filename, coll_name in _ENTITIES:
        path = src / filename
        if not path.exists():
            continue
        if drop_existing:
            bench.db[coll_name].drop()
        coll = bench.db[coll_name]
        inserted = 0
        t0 = time.perf_counter()
        for batch in _batched(_iter_jsonl(path), batch_size):
            ops = [InsertOne(doc) for doc in batch]
            if ops:
                coll.bulk_write(ops, ordered=False)
                inserted += len(ops)
        elapsed = time.perf_counter() - t0
        stats[coll_name] = LoadStats(inserted=inserted, elapsed_s=elapsed)

    if create_indexes:
        _create_indexes(bench)

    return stats


def _create_indexes(bench: MongoBench) -> None:
    """Create the parity indexes per ``docs/04-indexes.md``.

    Mongo creates B-tree indexes on the natural keys directly. Oracle's
    counterparts are function-based indexes on JSON_VALUE expressions —
    different mechanics, same logical access path. The benchmark
    deliberately gives both engines equivalent index coverage so the
    comparison reflects engine architecture, not index choice.
    """
    bench.db.customers.create_index([("customer_id", 1)], unique=True)
    bench.db.customers.create_index([("region_id", 1)])
    bench.db.products.create_index([("product_id", 1)], unique=True)
    bench.db.products.create_index([("category_id", 1)])
    bench.db.products.create_index([("sku", 1)], unique=True)
    bench.db.categories.create_index([("category_id", 1)], unique=True)
    bench.db.categories.create_index([("parent_id", 1)])
    bench.db.regions.create_index([("region_id", 1)], unique=True)
    bench.db.suppliers.create_index([("supplier_id", 1)], unique=True)
    bench.db.orders.create_index([("order_date", 1)])
    bench.db.orders.create_index([("customer_id", 1), ("order_date", 1)])
    bench.db.orders.create_index([("status", 1)])
    bench.db.orders.create_index([("line_items.product_id", 1)])


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Parse JSONL with type-coercion for fields the schema declares as
    datetime / numeric.

    The generator emits ISO 8601 strings for datetimes and ``Decimal``-as-string
    for prices (so JSONL round-trips without precision drift). MongoDB doesn't
    auto-convert those at insert time, so we coerce here. The same data on
    Oracle is loaded via typed columns; this keeps the engines comparable.
    """
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                yield _coerce_types(json.loads(stripped))


_DATETIME_FIELDS = frozenset({"signup_date", "order_date", "event_at"})
_NUMERIC_FIELDS = frozenset({"price", "unit_price", "discount", "extended_price"})


def _coerce_types(obj: Any) -> Any:
    """Walk a parsed JSONL object and coerce known datetime / numeric fields."""
    from datetime import datetime

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _DATETIME_FIELDS and isinstance(v, str):
                try:
                    out[k] = datetime.fromisoformat(v)
                except ValueError:
                    out[k] = v
            elif k in _NUMERIC_FIELDS and isinstance(v, str):
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
            else:
                out[k] = _coerce_types(v)
        return out
    if isinstance(obj, list):
        return [_coerce_types(v) for v in obj]
    return obj


def _batched(iterable: Iterator[dict[str, Any]], n: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch
