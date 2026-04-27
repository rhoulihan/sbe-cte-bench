"""S10 — Top-N optimization."""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S10TopN(ScenarioBase):
    id: ClassVar[str] = "S10"
    title: ClassVar[str] = "Top-N optimization with downstream stages"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="A-top-n-alone", parameters={"variant": "A"}),
            Variant(label="B-top-n-lookup", parameters={"variant": "B"}),
            Variant(label="C-top-n-facet", parameters={"variant": "C"}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        v = variant or Variant(label="A-top-n-alone", parameters={"variant": "A"})
        kind = v.parameters["variant"]

        # Pre-project per-order revenue so the subsequent $group can sum
        # across orders correctly (raw "$line_items.extended_price" in $sum
        # returns 0 because Mongo doesn't auto-flatten arrays in accumulator
        # context).
        prefix: list[dict[str, Any]] = [
            {"$match": {"order_date": {"$gte": datetime(2024, 8, 1, tzinfo=UTC)}}},
            {
                "$project": {
                    "customer_id": 1,
                    "order_revenue": {"$sum": "$line_items.extended_price"},
                }
            },
            {
                "$group": {
                    "_id": "$customer_id",
                    "total_revenue": {"$sum": "$order_revenue"},
                }
            },
            {"$sort": {"total_revenue": -1}},
            {"$limit": 100},
        ]
        if kind == "A":
            return [
                *prefix,
                {
                    "$project": {
                        "_id": 0,
                        "customer_id": "$_id",
                        "total_revenue": 1,
                    }
                },
            ]
        if kind == "B":
            return [
                *prefix,
                {
                    "$lookup": {
                        "from": "customers",
                        "localField": "_id",
                        "foreignField": "customer_id",
                        "as": "c",
                    }
                },
                {"$unwind": "$c"},
                {
                    "$project": {
                        "_id": 0,
                        "customer_id": "$_id",
                        "total_revenue": 1,
                        "name": "$c.name",
                        "region_id": "$c.region_id",
                    }
                },
            ]
        return [
            *prefix,
            {
                "$facet": {
                    "summary": [
                        {
                            "$group": {
                                "_id": None,
                                "total": {"$sum": "$revenue"},
                                "avg": {"$avg": "$revenue"},
                            }
                        }
                    ],
                    "detail": [
                        {
                            "$lookup": {
                                "from": "customers",
                                "localField": "_id",
                                "foreignField": "customer_id",
                                "as": "c",
                            }
                        },
                        {"$unwind": "$c"},
                    ],
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        v = variant or Variant(label="A-top-n-alone", parameters={"variant": "A"})
        kind = v.parameters["variant"]
        base = """
WITH revenue_by_customer AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         (SELECT SUM(li.extended_price)
          FROM JSON_TABLE(o.payload, '$.line_items[*]'
            COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-08-01 00:00:00 +00:00'
),
top_100 AS (
  SELECT customer_id, SUM(revenue) AS total_revenue
  FROM revenue_by_customer
  GROUP BY customer_id
  ORDER BY total_revenue DESC
  FETCH FIRST 100 ROWS ONLY
)
""".strip()
        if kind == "A":
            return base + "\nSELECT customer_id, total_revenue FROM top_100"
        if kind == "B":
            return (
                base
                + """
SELECT t.customer_id, t.total_revenue, c.name, c.region_id
FROM top_100 t JOIN customers c ON c.customer_id = t.customer_id
""".rstrip()
            )
        return (
            base
            + """
SELECT
  (SELECT JSON_OBJECT('total' VALUE SUM(total_revenue), 'avg' VALUE AVG(total_revenue))
   FROM top_100) AS summary,
  CURSOR(SELECT t.customer_id, t.total_revenue, c.name, c.region_id
         FROM top_100 t JOIN customers c ON c.customer_id = t.customer_id) AS detail
FROM dual
""".rstrip()
        )

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="A-top-n-alone", parameters={"variant": "A"})
        kind = v.parameters["variant"]
        return [
            Prediction(
                claim=f"variant {kind}: ratio reasonable",
                metric="ratio_mongo_to_oracle",
                operator="in",
                expected_value=[1.5, 5.0] if kind != "C" else [2.5, 8.0],
                confidence="medium-high",
            ),
        ]
