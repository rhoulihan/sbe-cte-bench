"""Deterministic data generator for the benchmark.

Generates the canonical e-commerce schema described in ``docs/03-data-model.md``
to JSONL files in a target directory. Each invocation with the same seed
produces byte-identical output (verified by property-based tests).

Output format: JSONL (one JSON object per line). The loader stage converts
this to BSON for MongoDB ingest and to CSV for SQL*Loader. We chose JSONL as
the intermediate format because:

- It's human-readable for debugging.
- Both ``pymongo`` and ``oracledb`` can ingest from it directly.
- Pydantic ``model_validate_json`` round-trips it cleanly.
- Hashing is straightforward (file-by-file SHA-256 of the bytes).

Determinism is guaranteed by:

- Single-source ``numpy.random.Generator(PCG64(seed))``.
- Stable iteration order: entities are emitted in fixed dependency order.
- Sorted dict keys in JSON serialization.
- ``Decimal`` (not float) for price-like fields.
- ISO 8601 datetime serialization in UTC.
"""

from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
from numpy.random import PCG64, Generator

GENERATOR_VERSION = "0.1.0"


class ScaleFactor(str, Enum):
    """Benchmark dataset sizes."""

    SF0_001 = "SF0.001"  # CI / fast iteration
    SF0_1 = "SF0.1"  # developer iteration
    SF1 = "SF1"  # primary benchmark scale


# Per docs/03-data-model.md scale factor table.
# SF0.001 is intentionally "1K orders / 1K customers" — small enough to run in
# seconds in CI, large enough to exercise every code path.
_SCALE_COUNTS: dict[ScaleFactor, dict[str, int]] = {
    ScaleFactor.SF0_001: {
        "regions": 50,
        "suppliers": 100,
        "categories_roots": 5,
        "categories_per_level": 4,
        "categories_levels": 4,
        "customers": 1_000,
        "products": 100,
        "orders": 1_000,
    },
    ScaleFactor.SF0_1: {
        "regions": 50,
        "suppliers": 1_000,
        "categories_roots": 5,
        "categories_per_level": 8,
        "categories_levels": 4,
        "customers": 10_000,
        "products": 1_000,
        "orders": 100_000,
    },
    ScaleFactor.SF1: {
        "regions": 50,
        "suppliers": 1_000,
        "categories_roots": 5,
        "categories_per_level": 8,
        "categories_levels": 4,
        "customers": 100_000,
        "products": 10_000,
        "orders": 1_000_000,
    },
}


@dataclass(frozen=True)
class Manifest:
    """Manifest of generated data.

    Includes the seed for reproducibility and per-file SHA-256 hashes for
    byte-stability verification.
    """

    seed: int
    scale: ScaleFactor
    counts: dict[str, int]
    hashes: dict[str, str]
    generator_version: str = GENERATOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "scale": self.scale.value,
            "counts": self.counts,
            "hashes": self.hashes,
            "generator_version": self.generator_version,
        }


def generate(
    *,
    scale: ScaleFactor = ScaleFactor.SF0_001,
    output_dir: Path | str,
    seed: int = 0xCAFE_F00D_BEEF_5BE & 0xFFFFFFFF,
) -> Manifest:
    """Generate a benchmark dataset.

    Args:
        scale: Dataset scale factor.
        output_dir: Directory to write JSONL files into. Created if missing.
        seed: PRNG seed; same seed → identical output.

    Returns:
        Manifest with per-file SHA-256 hashes.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = _SCALE_COUNTS[scale]
    rng = Generator(PCG64(seed))

    # Generate in dependency order. Each phase uses its own RNG slice via
    # spawn() so adding a new entity later doesn't shift downstream output.
    regions = _gen_regions(counts["regions"], rng=Generator(PCG64(seed + 1)))
    suppliers = _gen_suppliers(counts["suppliers"], rng=Generator(PCG64(seed + 2)))
    categories = _gen_categories(
        roots=counts["categories_roots"],
        per_level=counts["categories_per_level"],
        levels=counts["categories_levels"],
        rng=Generator(PCG64(seed + 3)),
    )
    products = _gen_products(
        counts["products"],
        category_ids=[c["category_id"] for c in categories],
        supplier_ids=[s["supplier_id"] for s in suppliers],
        rng=Generator(PCG64(seed + 4)),
    )
    customers = _gen_customers(
        counts["customers"],
        region_ids=[r["region_id"] for r in regions],
        rng=Generator(PCG64(seed + 5)),
    )
    orders = _gen_orders(
        counts["orders"],
        customer_ids=[c["customer_id"] for c in customers],
        products=products,
        rng=Generator(PCG64(seed + 6)),
    )

    written: dict[str, list[dict[str, Any]]] = {
        "regions.jsonl": regions,
        "suppliers.jsonl": suppliers,
        "categories.jsonl": categories,
        "products.jsonl": products,
        "customers.jsonl": customers,
        "orders.jsonl": orders,
    }

    hashes: dict[str, str] = {}
    for filename, rows in written.items():
        path = out / filename
        digest = _write_jsonl(path, rows)
        hashes[filename] = digest

    actual_counts = {
        "regions": len(regions),
        "suppliers": len(suppliers),
        "categories": len(categories),
        "products": len(products),
        "customers": len(customers),
        "orders": len(orders),
    }

    manifest = Manifest(seed=seed, scale=scale, counts=actual_counts, hashes=hashes)
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    # Don't include manifest.json in its own hashes block.
    _ = rng  # parent rng kept for future expansion; suppresses unused-arg lint.
    return manifest


# ─── Per-entity generators ────────────────────────────────────────────────


def _gen_regions(n: int, *, rng: Generator) -> list[dict[str, Any]]:
    countries = ["US", "DE", "FR", "UK", "JP", "BR", "IN", "AU", "CA", "NL"]
    return [
        {
            "region_id": i + 1,
            "name": f"Region-{i + 1:04d}",
            "country": countries[int(rng.integers(0, len(countries)))],
        }
        for i in range(n)
    ]


def _gen_suppliers(n: int, *, rng: Generator) -> list[dict[str, Any]]:
    countries = ["US", "DE", "FR", "UK", "CN", "TW", "KR", "JP", "VN", "MX"]
    tiers = ["preferred", "approved", "probation"]
    tier_weights = [0.6, 0.3, 0.1]
    return [
        {
            "supplier_id": i + 1,
            "name": f"Supplier-{i + 1:05d}",
            "country": countries[int(rng.integers(0, len(countries)))],
            "tier": str(rng.choice(tiers, p=tier_weights)),
        }
        for i in range(n)
    ]


def _gen_categories(
    *, roots: int, per_level: int, levels: int, rng: Generator
) -> list[dict[str, Any]]:
    """Build a multi-level taxonomy.

    Layout:
      Level 0: ``roots`` root categories (parent_id = None).
      Level k: each parent gets ``per_level`` children.
    """
    result: list[dict[str, Any]] = []
    next_id = 1
    current_level: list[int] = []
    for r in range(roots):
        result.append({"category_id": next_id, "name": f"Cat-L0-{r:03d}", "parent_id": None})
        current_level.append(next_id)
        next_id += 1

    for level in range(1, levels):
        new_level: list[int] = []
        for parent_id in current_level:
            for c in range(per_level):
                result.append(
                    {
                        "category_id": next_id,
                        "name": f"Cat-L{level}-{parent_id}-{c:03d}",
                        "parent_id": parent_id,
                    }
                )
                new_level.append(next_id)
                next_id += 1
        current_level = new_level

    _ = rng  # categories are deterministic by structure; rng reserved.
    return result


def _gen_products(
    n: int,
    *,
    category_ids: list[int],
    supplier_ids: list[int],
    rng: Generator,
) -> list[dict[str, Any]]:
    return [
        {
            "product_id": i + 1,
            "sku": _make_sku(i, rng),
            "name": f"Product-{i + 1:06d}",
            "category_id": int(rng.choice(category_ids)),
            "supplier_id": int(rng.choice(supplier_ids)),
            "price": _decimal_str(_lognormal_price(rng)),
            "attributes": {
                "color": str(rng.choice(["red", "green", "blue", "black", "white"])),
                "weight_kg": _decimal_str(round(float(rng.uniform(0.1, 25.0)), 3)),
                "specs": {
                    "depth": int(rng.integers(2, 8)),
                    "tags": [str(rng.choice(["new", "sale", "exclusive", "premium"]))],
                },
            },
        }
        for i in range(n)
    ]


def _gen_customers(n: int, *, region_ids: list[int], rng: Generator) -> list[dict[str, Any]]:
    tiers = ["bronze", "silver", "gold", "platinum"]
    tier_weights = [0.60, 0.25, 0.12, 0.03]
    out: list[dict[str, Any]] = []
    for i in range(n):
        signup = _random_datetime(rng, start_year=2018, end_year=2025)
        out.append(
            {
                "customer_id": i + 1,
                "name": f"Customer-{i + 1:07d}",
                "email": f"user{i + 1:07d}@example.com",
                "region_id": int(rng.choice(region_ids)),
                "signup_date": signup.isoformat(),
                "tier": str(rng.choice(tiers, p=tier_weights)),
                "metadata": {
                    "marketing": {
                        "campaigns": [
                            {
                                "id": f"camp-{int(rng.integers(0, 1000)):04d}",
                                "tracking": {
                                    "attribution": {
                                        "id": (
                                            f"attr-{int(rng.integers(0, 10000)):05d}"
                                        ),
                                    },
                                },
                            }
                            for _ in range(int(rng.integers(1, 4)))
                        ],
                    },
                    "prefs": {
                        "notifications": {
                            "email": {"daily_limit": int(rng.integers(0, 10))},
                        },
                    },
                },
            }
        )
    return out


def _gen_orders(
    n: int,
    *,
    customer_ids: list[int],
    products: list[dict[str, Any]],
    rng: Generator,
) -> list[dict[str, Any]]:
    statuses = ["pending", "shipped", "delivered", "cancelled", "returned"]
    status_weights = [0.05, 0.15, 0.65, 0.10, 0.05]
    payment_methods = ["card", "ach", "wire", "paypal", "crypto"]
    payment_weights = [0.70, 0.15, 0.05, 0.08, 0.02]
    currencies = ["USD", "EUR", "GBP", "JPY"]
    currency_weights = [0.75, 0.15, 0.06, 0.04]

    out: list[dict[str, Any]] = []
    for i in range(n):
        order_dt = _random_datetime(rng, start_year=2023, end_year=2025)
        n_items = int(rng.integers(1, 11))  # 1..10 line items
        line_items = []
        for li_idx in range(n_items):
            product = products[int(rng.integers(0, len(products)))]
            qty = int(rng.integers(1, 6))
            unit_price = Decimal(product["price"])
            discount = Decimal(str(round(float(rng.uniform(0, 0.3)), 4)))
            extended = (unit_price * qty * (Decimal("1") - discount)).quantize(Decimal("0.01"))
            line_items.append(
                {
                    "line_id": li_idx + 1,
                    "product_id": int(product["product_id"]),
                    "quantity": qty,
                    "unit_price": _decimal_str(unit_price),
                    "discount": _decimal_str(discount),
                    "extended_price": _decimal_str(extended),
                    "attrs": dict(product["attributes"]),
                }
            )

        out.append(
            {
                "order_id": i + 1,
                "customer_id": int(rng.choice(customer_ids)),
                "order_date": order_dt.isoformat(),
                "status": str(rng.choice(statuses, p=status_weights)),
                "currency": str(rng.choice(currencies, p=currency_weights)),
                "payment": {
                    "method": str(rng.choice(payment_methods, p=payment_weights)),
                    "transaction_id": f"txn-{int(rng.integers(0, 1_000_000)):07d}",
                },
                "shipping": {
                    "method": str(rng.choice(["standard", "express", "overnight"])),
                    "tracking_id": f"trk-{int(rng.integers(0, 1_000_000_000)):010d}",
                },
                "line_items": line_items,
                "notes": None,
                "audit": [
                    {
                        "event_at": order_dt.isoformat(),
                        "event_type": "created",
                        "actor": "system",
                    }
                ],
            }
        )
    return out


# ─── Helpers ──────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    """Write rows to JSONL with sorted keys; return SHA-256 digest of bytes."""
    hasher = hashlib.sha256()
    with path.open("wb") as f:
        for row in rows:
            line = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            f.write(line)
            hasher.update(line)
    return hasher.hexdigest()


def _make_sku(idx: int, rng: Generator) -> str:
    prefix = "".join(str(rng.choice(list(string.ascii_uppercase))) for _ in range(3))
    return f"{prefix}-{idx + 1:06d}"


def _lognormal_price(rng: Generator) -> Decimal:
    """Lognormal-distributed prices in roughly $5 to $5000."""
    raw = float(np.exp(rng.normal(loc=3.5, scale=1.0)))
    clamped = max(5.0, min(5000.0, raw))
    return Decimal(str(round(clamped, 2)))


def _decimal_str(value: Decimal | float | int) -> str:
    """Stable string form of a numeric value, suitable for JSON serialization."""
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def _random_datetime(rng: Generator, *, start_year: int, end_year: int) -> datetime:
    """Uniform random datetime in the interval [start_year, end_year)."""
    start = datetime(start_year, 1, 1, tzinfo=UTC)
    end = datetime(end_year, 12, 31, 23, 59, 59, tzinfo=UTC)
    span_seconds = int((end - start).total_seconds())
    offset_seconds = int(rng.integers(0, span_seconds))
    return start + timedelta(seconds=offset_seconds)
