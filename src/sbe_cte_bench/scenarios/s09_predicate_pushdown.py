"""S09 — Predicate pushdown / join reordering."""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register

_EMEA_REGION_IDS = list(range(1, 11))


@register
class S09PredicatePushdown(ScenarioBase):
    id: ClassVar[str] = "S09"
    title: ClassVar[str] = "Predicate pushdown / join reordering"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="A-well-ordered", parameters={"variant": "A"}),
            Variant(label="B-lookup-first", parameters={"variant": "B"}),
            Variant(label="C-facet-wrap", parameters={"variant": "C"}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        v = variant or Variant(label="A-well-ordered", parameters={"variant": "A"})
        kind = v.parameters["variant"]

        match_date = {
            "order_date": {
                "$gte": datetime(2024, 10, 1, tzinfo=UTC),
                "$lt": datetime(2025, 1, 1, tzinfo=UTC),
            }
        }
        lookup_customer = {
            "$lookup": {
                "from": "customers",
                "localField": "customer_id",
                "foreignField": "customer_id",
                "as": "c",
            }
        }
        match_premium = {
            "$match": {
                "c.tier": "platinum",
                "c.region_id": {"$in": _EMEA_REGION_IDS},
                "line_items.extended_price": {"$gte": 500},
            }
        }
        project = {
            "$project": {
                "order_id": 1,
                "customer_name": "$c.name",
                "product_id": "$line_items.product_id",
                "extended_price": "$line_items.extended_price",
            }
        }

        if kind == "A":
            return [
                {"$match": match_date},
                lookup_customer,
                {"$unwind": "$c"},
                {
                    "$match": {
                        "c.tier": "platinum",
                        "c.region_id": {"$in": _EMEA_REGION_IDS},
                    }
                },
                {"$unwind": "$line_items"},
                {"$match": {"line_items.extended_price": {"$gte": 500}}},
                project,
            ]
        if kind == "B":
            return [
                lookup_customer,
                {"$unwind": "$c"},
                {"$unwind": "$line_items"},
                {"$match": {**match_date, **match_premium["$match"]}},
                project,
            ]
        # C: facet wrapping disables predicate pushdown.
        return [
            {"$match": match_date},
            {
                "$facet": {
                    "enriched": [
                        lookup_customer,
                        {"$unwind": "$c"},
                        {"$unwind": "$line_items"},
                    ]
                }
            },
            {"$unwind": "$enriched"},
            {"$replaceRoot": {"newRoot": "$enriched"}},
            match_premium,
            project,
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
WITH premium_emea_orders AS (
  SELECT JSON_VALUE(o.payload, '$.order_id'    RETURNING NUMBER) AS order_id,
         JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-10-01 00:00:00 +00:00'
    AND JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE)  < TIMESTAMP '2025-01-01 00:00:00 +00:00'
),
joined AS (
  SELECT po.order_id, po.customer_id, c.name AS customer_name
  FROM premium_emea_orders po
  JOIN customers c ON c.customer_id = po.customer_id
  WHERE c.tier = 'platinum'
    AND c.region_id IN (SELECT region_id FROM regions WHERE country IN ('DE','FR','IT','ES','UK','PL','NL','BE','SE','DK'))
)
SELECT j.order_id, j.customer_name, li.product_id, li.extended_price
FROM joined j
JOIN orders_doc o ON JSON_VALUE(o.payload, '$.order_id' RETURNING NUMBER) = j.order_id,
     JSON_TABLE(o.payload, '$.line_items[*]'
       COLUMNS (
         product_id NUMBER PATH '$.product_id',
         extended_price NUMBER PATH '$.extended_price'
       )) li
WHERE li.extended_price >= 500
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="A-well-ordered", parameters={"variant": "A"})
        kind = v.parameters["variant"]
        if kind == "A":
            return [
                Prediction(
                    claim="well-ordered: ratio in [3, 15]",
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[3.0, 15.0],
                    confidence="high",
                ),
            ]
        if kind == "B":
            return [
                Prediction(
                    claim="lookup-first anti-pattern: ratio >= 30x",
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=30.0,
                    confidence="high",
                ),
            ]
        return [
            Prediction(
                claim="$facet wrap kills pushdown: ratio >= 50x",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=50.0,
                confidence="high",
            ),
        ]
