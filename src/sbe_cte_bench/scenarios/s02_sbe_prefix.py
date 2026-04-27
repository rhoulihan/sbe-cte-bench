"""S02 — SBE-prefix best case.

Per ``docs/scenarios/S02-sbe-prefix-best-case.md``. Multi-stage pipeline that
runs entirely in SBE: $match -> $group -> $sort -> $limit -> $lookup -> $project.
Mongo's best case; Oracle should still win on planning freedom.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S02SbePrefix(ScenarioBase):
    id: ClassVar[str] = "S02"
    title: ClassVar[str] = "SBE-prefix best case"

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        return [
            {"$match": {"order_date": {"$gte": datetime(2024, 8, 1, tzinfo=UTC)}}},
            {
                "$unwind": "$line_items",
            },
            {
                "$group": {
                    "_id": "$customer_id",
                    "revenue_90d": {"$sum": "$line_items.extended_price"},
                    "order_count": {"$sum": 1},
                }
            },
            {"$sort": {"revenue_90d": -1}},
            {"$limit": 100},
            {
                "$lookup": {
                    "from": "customers",
                    "localField": "_id",
                    "foreignField": "customer_id",
                    "as": "customer",
                }
            },
            {"$unwind": "$customer"},
            {
                "$project": {
                    "_id": 0,
                    "customer_id": "$_id",
                    "customer_name": "$customer.name",
                    "tier": "$customer.tier",
                    "region_id": "$customer.region_id",
                    "revenue_90d": 1,
                    "order_count": 1,
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
WITH recent_orders AS (
  SELECT
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    li.extended_price
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (extended_price NUMBER PATH '$.extended_price')) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-08-01 00:00:00 +00:00'
),
top_clients AS (
  SELECT customer_id,
         SUM(extended_price) AS revenue_90d,
         COUNT(*)            AS order_count
  FROM recent_orders
  GROUP BY customer_id
  ORDER BY revenue_90d DESC
  FETCH FIRST 100 ROWS ONLY
)
SELECT t.customer_id,
       c.name AS customer_name,
       c.tier,
       c.region_id,
       t.revenue_90d,
       t.order_count
FROM top_clients t
JOIN customers c ON c.customer_id = t.customer_id
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        return [
            Prediction(
                claim="Mongo's best case; ratio in [1.1, 2.0]",
                metric="ratio_mongo_to_oracle",
                operator="in",
                expected_value=[1.1, 2.0],
                confidence="high",
            ),
            Prediction(
                claim="entire pipeline runs in SBE; no classic boundary",
                metric="mongo_classic_boundary_at_stage",
                operator="==",
                expected_value=None,
                confidence="high",
            ),
            Prediction(
                claim="oracle CTEs are inlined (no TEMP TABLE TRANSFORMATION)",
                metric="oracle_has_materialized_ctes",
                operator="==",
                expected_value=False,
                confidence="high",
            ),
        ]
