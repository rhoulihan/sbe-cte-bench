"""S06 — $lookup against sharded foreign collection. Requires sharded topology.

Per docs/scenarios/S06-lookup-sharded.md.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S06LookupSharded(ScenarioBase):
    id: ClassVar[str] = "S06"
    title: ClassVar[str] = "$lookup against sharded foreign collection"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="unsharded", parameters={"topology": "standard"}),
            Variant(label="sharded-2", parameters={"topology": "sharded"}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        return [
            {"$match": {"order_date": {"$gte": datetime(2024, 1, 1, tzinfo=UTC)}}},
            {
                "$lookup": {
                    "from": "customers",
                    "localField": "customer_id",
                    "foreignField": "customer_id",
                    "as": "customer",
                }
            },
            {"$unwind": "$customer"},
            {
                "$project": {
                    "order_id": 1,
                    "order_date": 1,
                    "customer_name": "$customer.name",
                    "tier": "$customer.tier",
                    "region_id": "$customer.region_id",
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
SELECT JSON_VALUE(o.payload, '$.order_id'    RETURNING NUMBER) AS order_id,
       JSON_VALUE(o.payload, '$.order_date'  RETURNING TIMESTAMP WITH TIME ZONE) AS order_date,
       c.name AS customer_name,
       c.tier,
       c.region_id
FROM orders_doc o
JOIN customers c
  ON c.customer_id = JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER)
WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-01-01 00:00:00 +00:00'
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="sharded-2", parameters={"topology": "sharded"})
        is_sharded = v.parameters["topology"] == "sharded"
        if is_sharded:
            return [
                Prediction(
                    claim="sharded foreign: ratio >= 10x (classic-engine fallback + scatter-gather)",
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=10.0,
                    confidence="very high",
                ),
                Prediction(
                    claim="explain shows classic-engine $lookup (no EQ_LOOKUP)",
                    metric="mongo_uses_eq_lookup",
                    operator="==",
                    expected_value=False,
                    confidence="very high",
                ),
            ]
        return [
            Prediction(
                claim="unsharded baseline: ratio in [1.5, 4.0]",
                metric="ratio_mongo_to_oracle",
                operator="in",
                expected_value=[1.5, 4.0],
                confidence="high",
            ),
        ]
