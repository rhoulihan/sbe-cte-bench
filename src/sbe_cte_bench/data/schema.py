"""Pydantic models for benchmark entities.

Acts as the single source of truth for the schema described in
``docs/03-data-model.md``. Both the generator and the loaders use these
models — generator to emit valid data, loaders to validate inbound from
``data/generated/``.

Decimal precision: ``price``, ``unit_price``, ``discount``, ``extended_price``
are all stored as :class:`decimal.Decimal` for byte-stable JSON output and to
avoid float-precision drift when round-tripped through OSON.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CustomerTier = Literal["bronze", "silver", "gold", "platinum"]
SupplierTier = Literal["preferred", "approved", "probation"]
OrderStatus = Literal["pending", "shipped", "delivered", "cancelled", "returned"]
PaymentMethod = Literal["card", "ach", "wire", "paypal", "crypto"]


class _Base(BaseModel):
    """Base config for all entities. Forbids unknown fields to catch drift."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Region(_Base):
    region_id: int
    name: str
    country: str = Field(min_length=2, max_length=3)


class Supplier(_Base):
    supplier_id: int
    name: str
    country: str = Field(min_length=2, max_length=3)
    tier: SupplierTier


class Category(_Base):
    category_id: int
    name: str
    parent_id: int | None


class Customer(_Base):
    customer_id: int
    name: str
    email: str
    region_id: int
    signup_date: datetime
    tier: CustomerTier
    metadata: dict[str, Any]


class Product(_Base):
    product_id: int
    sku: str
    name: str
    category_id: int
    supplier_id: int
    price: Decimal
    attributes: dict[str, Any]


class LineItem(_Base):
    line_id: int
    product_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal
    discount: Decimal = Field(ge=0, le=Decimal("1"))
    extended_price: Decimal
    attrs: dict[str, Any]


class AuditEvent(_Base):
    event_at: datetime
    event_type: str
    actor: str


class Order(_Base):
    order_id: int
    customer_id: int
    order_date: datetime
    status: OrderStatus
    currency: str = Field(min_length=3, max_length=3)
    payment: dict[str, Any]
    shipping: dict[str, Any]
    line_items: list[LineItem]
    notes: str | None
    audit: list[AuditEvent]
