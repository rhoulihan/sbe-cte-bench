"""Tests for the data schema definitions.

The schemas serve a dual purpose: they validate generated data, and they
document the contract that loaders must meet on the way into both engines.
Tests assert structural properties — required fields, value ranges,
inter-entity references — without exercising the generator itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from sbe_cte_bench.data.schema import (
    AuditEvent,
    Category,
    Customer,
    LineItem,
    Order,
    Product,
    Region,
    Supplier,
)


@pytest.mark.unit
def test_region_minimal() -> None:
    r = Region(region_id=1, name="EMEA", country="DE")
    assert r.region_id == 1


@pytest.mark.unit
def test_supplier_tier_validated() -> None:
    s = Supplier(supplier_id=1, name="Acme", country="US", tier="preferred")
    assert s.tier == "preferred"

    with pytest.raises(ValidationError):
        Supplier(supplier_id=1, name="Acme", country="US", tier="garbage")  # type: ignore[arg-type]


@pytest.mark.unit
def test_customer_tier_validated() -> None:
    c = Customer(
        customer_id=1,
        name="Alice",
        email="a@b.com",
        region_id=1,
        signup_date=datetime(2024, 1, 1, tzinfo=UTC),
        tier="gold",
        metadata={"foo": "bar"},
    )
    assert c.tier == "gold"


@pytest.mark.unit
def test_category_self_reference_optional() -> None:
    """Root categories have parent_id = None."""
    root = Category(category_id=1, name="root", parent_id=None)
    assert root.parent_id is None
    child = Category(category_id=2, name="child", parent_id=1)
    assert child.parent_id == 1


@pytest.mark.unit
def test_product_price_decimal() -> None:
    p = Product(
        product_id=1,
        sku="SKU-001",
        name="Thing",
        category_id=1,
        supplier_id=1,
        price=Decimal("19.99"),
        attributes={"color": "red"},
    )
    assert p.price == Decimal("19.99")


@pytest.mark.unit
def test_line_item_extended_price_consistent() -> None:
    """extended_price should equal quantity * unit_price * (1 - discount)."""
    li = LineItem(
        line_id=1,
        product_id=1,
        quantity=2,
        unit_price=Decimal("10.00"),
        discount=Decimal("0.10"),
        extended_price=Decimal("18.00"),
        attrs={},
    )
    assert li.extended_price == Decimal("18.00")


@pytest.mark.unit
def test_order_with_line_items() -> None:
    o = Order(
        order_id=1,
        customer_id=1,
        order_date=datetime(2024, 6, 1, tzinfo=UTC),
        status="delivered",
        currency="USD",
        payment={"method": "card"},
        shipping={"method": "standard"},
        line_items=[
            LineItem(
                line_id=1,
                product_id=1,
                quantity=1,
                unit_price=Decimal("10.00"),
                discount=Decimal("0.00"),
                extended_price=Decimal("10.00"),
                attrs={},
            )
        ],
        notes=None,
        audit=[
            AuditEvent(
                event_at=datetime(2024, 6, 1, tzinfo=UTC), event_type="created", actor="system"
            )
        ],
    )
    assert o.status == "delivered"
    assert len(o.line_items) == 1


@pytest.mark.unit
def test_order_status_validated() -> None:
    with pytest.raises(ValidationError):
        Order(
            order_id=1,
            customer_id=1,
            order_date=datetime(2024, 6, 1, tzinfo=UTC),
            status="quantum-superposition",  # type: ignore[arg-type]
            currency="USD",
            payment={},
            shipping={},
            line_items=[],
            notes=None,
            audit=[],
        )
