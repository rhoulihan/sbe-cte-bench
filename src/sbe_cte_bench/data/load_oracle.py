"""Oracle loader.

Reads JSONL files produced by ``generator.py`` and inserts them into Oracle
tables. The relational entities (regions, suppliers, categories, customers,
products) load via direct INSERT batches; orders load into both the JSON
column on ``orders_doc`` and (for JDV testing) into normalized
``orders_rel`` + ``order_line_items_rel``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sbe_cte_bench.drivers.oracle import OracleBench


@dataclass(frozen=True)
class OracleLoadStats:
    table: str
    inserted: int
    elapsed_s: float


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE regions (
        region_id  NUMBER PRIMARY KEY,
        name       VARCHAR2(64) NOT NULL,
        country    VARCHAR2(3)  NOT NULL
    )
    """,
    """
    CREATE TABLE suppliers (
        supplier_id NUMBER PRIMARY KEY,
        name        VARCHAR2(128) NOT NULL,
        country     VARCHAR2(3)   NOT NULL,
        tier        VARCHAR2(16)  NOT NULL
    )
    """,
    """
    CREATE TABLE categories (
        category_id NUMBER PRIMARY KEY,
        name        VARCHAR2(128) NOT NULL,
        parent_id   NUMBER REFERENCES categories(category_id)
    )
    """,
    """
    CREATE TABLE customers (
        customer_id NUMBER PRIMARY KEY,
        name        VARCHAR2(128) NOT NULL,
        email       VARCHAR2(128) NOT NULL,
        region_id   NUMBER REFERENCES regions(region_id),
        signup_date TIMESTAMP WITH TIME ZONE NOT NULL,
        tier        VARCHAR2(16)  NOT NULL,
        metadata    JSON
    )
    """,
    """
    CREATE TABLE products (
        product_id  NUMBER PRIMARY KEY,
        sku         VARCHAR2(32) NOT NULL UNIQUE,
        name        VARCHAR2(128) NOT NULL,
        category_id NUMBER REFERENCES categories(category_id),
        supplier_id NUMBER REFERENCES suppliers(supplier_id),
        price       NUMBER(10,2) NOT NULL,
        attributes  JSON
    )
    """,
    """
    CREATE TABLE orders_doc (
        order_id NUMBER PRIMARY KEY,
        payload  JSON
    )
    """,
    """
    CREATE TABLE customer_summary (
        customer_id NUMBER PRIMARY KEY,
        revenue     NUMBER(18,2)
    )
    """,
)


def create_schema(bench: OracleBench) -> None:
    """Create all benchmark tables. Idempotent — drops existing tables first."""
    drop_order = (
        "customer_summary",
        "orders_doc",
        "products",
        "customers",
        "categories",
        "suppliers",
        "regions",
    )
    import contextlib

    with bench.acquire() as conn, conn.cursor() as cur:
        for table in drop_order:
            # DROP-if-exists semantics: error means table didn't exist; ignore.
            with contextlib.suppress(Exception):
                cur.execute(f"DROP TABLE {table} CASCADE CONSTRAINTS")
        for ddl in _DDL_STATEMENTS:
            cur.execute(ddl)
        conn.commit()


def load_oracle(
    *,
    bench: OracleBench,
    data_dir: Path | str,
    batch_size: int = 1000,
    create_indexes: bool = True,
) -> dict[str, OracleLoadStats]:
    """Load all benchmark entities from ``data_dir`` into the Oracle schema."""
    src = Path(data_dir)
    stats: dict[str, OracleLoadStats] = {}

    stats["regions"] = _load_regions(bench, src / "regions.jsonl", batch_size)
    stats["suppliers"] = _load_suppliers(bench, src / "suppliers.jsonl", batch_size)
    stats["categories"] = _load_categories(bench, src / "categories.jsonl", batch_size)
    stats["customers"] = _load_customers(bench, src / "customers.jsonl", batch_size)
    stats["products"] = _load_products(bench, src / "products.jsonl", batch_size)
    stats["orders_doc"] = _load_orders_doc(bench, src / "orders.jsonl", batch_size)

    if create_indexes:
        _create_indexes(bench)

    return stats


_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX ix_cust_region ON customers (region_id)",
    "CREATE INDEX ix_prod_category ON products (category_id)",
    "CREATE INDEX ix_prod_sku ON products (sku)",
    "CREATE INDEX ix_cat_parent ON categories (parent_id)",
    # Function-based indexes on common JSON paths in orders_doc — Mongo's
    # equivalent multi-key indexes are on the same logical fields.
    "CREATE INDEX ix_ord_date ON orders_doc "
    "(JSON_VALUE(payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE))",
    "CREATE INDEX ix_ord_customer ON orders_doc "
    "(JSON_VALUE(payload, '$.customer_id' RETURNING NUMBER))",
    "CREATE INDEX ix_ord_status ON orders_doc "
    "(JSON_VALUE(payload, '$.status' RETURNING VARCHAR2(16)))",
    # Multi-value index on line_items.product_id (Oracle 21c+ feature).
    # Allows JSON path queries that filter on array element fields.
    "CREATE MULTIVALUE INDEX ix_ord_li_product ON orders_doc o "
    "(o.payload.line_items.product_id.numberOnly())",
)


def _create_indexes(bench: OracleBench) -> None:
    """Create the parity indexes per ``docs/04-indexes.md``.

    Failures are tolerated (log + continue) so reload is idempotent — if an
    index already exists from a prior load, that's fine.
    """
    import contextlib

    with bench.acquire() as conn, conn.cursor() as cur:
        for ddl in _INDEX_DDL:
            with contextlib.suppress(Exception):
                cur.execute(ddl)
        # Refresh statistics so the CBO knows about new indexes.
        cur.execute("BEGIN DBMS_STATS.GATHER_SCHEMA_STATS(USER, NO_INVALIDATE => FALSE); END;")
        conn.commit()


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


def _executemany_with_stats(
    bench: OracleBench, sql: str, batches: Iterator[list[Any]], table: str
) -> OracleLoadStats:
    inserted = 0
    t0 = time.perf_counter()
    with bench.acquire() as conn, conn.cursor() as cur:
        for batch in batches:
            if not batch:
                continue
            cur.executemany(sql, batch)
            inserted += len(batch)
        conn.commit()
    return OracleLoadStats(table=table, inserted=inserted, elapsed_s=time.perf_counter() - t0)


def _batches(rows: Iterator[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _load_regions(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    rows = ((r["region_id"], r["name"], r["country"]) for r in _iter_jsonl(path))
    return _executemany_with_stats(
        bench,
        "INSERT INTO regions (region_id, name, country) VALUES (:1, :2, :3)",
        _batches(rows, batch_size),
        "regions",
    )


def _load_suppliers(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    rows = ((r["supplier_id"], r["name"], r["country"], r["tier"]) for r in _iter_jsonl(path))
    return _executemany_with_stats(
        bench,
        "INSERT INTO suppliers (supplier_id, name, country, tier) VALUES (:1, :2, :3, :4)",
        _batches(rows, batch_size),
        "suppliers",
    )


def _load_categories(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    # Insert in two passes to satisfy the parent_id FK: roots first, then children.
    rows_iter = list(_iter_jsonl(path))
    roots = [r for r in rows_iter if r["parent_id"] is None]
    children = [r for r in rows_iter if r["parent_id"] is not None]

    sql = "INSERT INTO categories (category_id, name, parent_id) VALUES (:1, :2, :3)"
    inserted = 0
    t0 = time.perf_counter()
    with bench.acquire() as conn, conn.cursor() as cur:
        for batch in _batches(
            iter((r["category_id"], r["name"], r["parent_id"]) for r in roots),
            batch_size,
        ):
            cur.executemany(sql, batch)
            inserted += len(batch)
        for batch in _batches(
            iter((r["category_id"], r["name"], r["parent_id"]) for r in children),
            batch_size,
        ):
            cur.executemany(sql, batch)
            inserted += len(batch)
        conn.commit()
    return OracleLoadStats(
        table="categories", inserted=inserted, elapsed_s=time.perf_counter() - t0
    )


def _load_customers(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    from datetime import datetime

    sql = (
        "INSERT INTO customers (customer_id, name, email, region_id, signup_date, tier, metadata) "
        "VALUES (:1, :2, :3, :4, :5, :6, :7)"
    )
    rows = (
        (
            r["customer_id"],
            r["name"],
            r["email"],
            r["region_id"],
            datetime.fromisoformat(r["signup_date"]),  # ISO 8601 -> datetime
            r["tier"],
            json.dumps(r["metadata"]),
        )
        for r in _iter_jsonl(path)
    )
    return _executemany_with_stats(bench, sql, _batches(rows, batch_size), "customers")


def _load_products(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    sql = (
        "INSERT INTO products "
        "(product_id, sku, name, category_id, supplier_id, price, attributes) "
        "VALUES (:1, :2, :3, :4, :5, :6, :7)"
    )
    rows = (
        (
            r["product_id"],
            r["sku"],
            r["name"],
            r["category_id"],
            r["supplier_id"],
            float(r["price"]),
            json.dumps(r["attributes"]),
        )
        for r in _iter_jsonl(path)
    )
    return _executemany_with_stats(bench, sql, _batches(rows, batch_size), "products")


def _load_orders_doc(bench: OracleBench, path: Path, batch_size: int) -> OracleLoadStats:
    sql = "INSERT INTO orders_doc (order_id, payload) VALUES (:1, :2)"
    rows = ((r["order_id"], json.dumps(r)) for r in _iter_jsonl(path))
    return _executemany_with_stats(bench, sql, _batches(rows, batch_size), "orders_doc")
