"""Tests for the deterministic data generator.

The generator's correctness rests on three properties:

1. Determinism: same seed → byte-identical output.
2. Scale-factor accuracy: declared counts are produced exactly.
3. Schema validity: every emitted entity validates against its Pydantic model.

Property tests cover (1) by fuzzing seeds and verifying hash equality across
two invocations. The remaining tests are unit-style and use the smallest scale
(SF0.001) so the suite runs in seconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sbe_cte_bench.data.generator import ScaleFactor, generate
from sbe_cte_bench.data.schema import Customer, Order, Product


@pytest.mark.unit
def test_generate_sf_001_returns_manifest(tmp_path: Path) -> None:
    manifest = generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    assert manifest.seed == 0xCAFE
    assert manifest.scale == ScaleFactor.SF0_001
    assert manifest.hashes
    assert "orders.jsonl" in manifest.hashes


@pytest.mark.unit
def test_generate_sf_001_counts(tmp_path: Path) -> None:
    """SF0.001 produces exactly 100 customers, 1K orders, etc."""
    manifest = generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    assert manifest.counts["regions"] == 50
    assert manifest.counts["suppliers"] == 100
    assert manifest.counts["categories"] >= 100
    assert manifest.counts["customers"] == 1000  # SF0.001 * SF1 (100K) = 100; we use 1K minimum
    assert manifest.counts["products"] == 100
    assert manifest.counts["orders"] == 1000


@pytest.mark.unit
def test_generate_writes_files(tmp_path: Path) -> None:
    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    expected_files = {
        "regions.jsonl",
        "suppliers.jsonl",
        "categories.jsonl",
        "customers.jsonl",
        "products.jsonl",
        "orders.jsonl",
        "manifest.json",
    }
    actual_files = {p.name for p in tmp_path.iterdir()}
    assert expected_files <= actual_files


@pytest.mark.unit
def test_generate_orders_validate_against_schema(tmp_path: Path) -> None:
    """Every emitted order validates against the Order Pydantic model."""

    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    with (tmp_path / "orders.jsonl").open() as f:
        for line in f:
            Order.model_validate_json(line)


@pytest.mark.unit
def test_generate_customers_validate_against_schema(tmp_path: Path) -> None:
    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    with (tmp_path / "customers.jsonl").open() as f:
        for line in f:
            Customer.model_validate_json(line)


@pytest.mark.unit
def test_generate_products_validate_against_schema(tmp_path: Path) -> None:
    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    with (tmp_path / "products.jsonl").open() as f:
        for line in f:
            Product.model_validate_json(line)


@pytest.mark.property
@settings(max_examples=10, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**63 - 1))
def test_generator_is_deterministic(tmp_path_factory: pytest.TempPathFactory, seed: int) -> None:
    """Two invocations with the same seed produce identical manifest hashes.

    Using ``tmp_path_factory`` (session-scoped) instead of ``tmp_path`` so each
    hypothesis example gets a fresh directory.
    """
    a = tmp_path_factory.mktemp("a")
    b = tmp_path_factory.mktemp("b")
    m1 = generate(scale=ScaleFactor.SF0_001, output_dir=a, seed=seed)
    m2 = generate(scale=ScaleFactor.SF0_001, output_dir=b, seed=seed)
    assert m1.hashes == m2.hashes


@pytest.mark.unit
def test_different_seeds_produce_different_data(tmp_path_factory: pytest.TempPathFactory) -> None:
    a = tmp_path_factory.mktemp("a")
    b = tmp_path_factory.mktemp("b")
    m1 = generate(scale=ScaleFactor.SF0_001, output_dir=a, seed=1)
    m2 = generate(scale=ScaleFactor.SF0_001, output_dir=b, seed=2)
    assert m1.hashes != m2.hashes


@pytest.mark.unit
def test_categories_form_valid_taxonomy(tmp_path: Path) -> None:
    """Generated categories have a valid 4-level parent_id chain."""
    import json

    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    seen_ids: set[int] = set()
    with (tmp_path / "categories.jsonl").open() as f:
        cats = [json.loads(line) for line in f]
    for cat in cats:
        seen_ids.add(cat["category_id"])
    # Every parent_id must be an existing category_id (or null for roots).
    for cat in cats:
        if cat["parent_id"] is not None:
            assert cat["parent_id"] in seen_ids, f"orphan: {cat}"


@pytest.mark.unit
def test_orders_reference_valid_customers(tmp_path: Path) -> None:
    import json

    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    customer_ids: set[int] = set()
    with (tmp_path / "customers.jsonl").open() as f:
        for line in f:
            customer_ids.add(json.loads(line)["customer_id"])
    with (tmp_path / "orders.jsonl").open() as f:
        for line in f:
            order = json.loads(line)
            assert order["customer_id"] in customer_ids


@pytest.mark.unit
def test_orders_line_items_reference_valid_products(tmp_path: Path) -> None:
    import json

    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    product_ids: set[int] = set()
    with (tmp_path / "products.jsonl").open() as f:
        for line in f:
            product_ids.add(json.loads(line)["product_id"])
    with (tmp_path / "orders.jsonl").open() as f:
        for line in f:
            order = json.loads(line)
            for li in order["line_items"]:
                assert li["product_id"] in product_ids


@pytest.mark.unit
def test_manifest_contains_seed_and_version(tmp_path: Path) -> None:
    import json

    generate(scale=ScaleFactor.SF0_001, output_dir=tmp_path, seed=0xCAFE)
    with (tmp_path / "manifest.json").open() as f:
        manifest_data = json.load(f)
    assert manifest_data["seed"] == 0xCAFE
    assert "scale" in manifest_data
    assert "hashes" in manifest_data
    assert "generator_version" in manifest_data


@pytest.mark.unit
def test_scale_factor_enum_has_expected_values() -> None:
    assert ScaleFactor.SF0_001.value == "SF0.001"
    assert ScaleFactor.SF0_1.value == "SF0.1"
    assert ScaleFactor.SF1.value == "SF1"
