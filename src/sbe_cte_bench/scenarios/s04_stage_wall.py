"""S04 — 100 MB per-stage memory cap. Per docs/scenarios/S04-100mb-stage-wall.md."""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S04StageWall(ScenarioBase):
    id: ClassVar[str] = "S04"
    title: ClassVar[str] = "100 MB per-stage memory cap"
    set_valued_paths: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label=f"ws={mb}MB", parameters={"working_set_mb": mb})
            for mb in (25, 50, 75, 100, 150, 200, 250)
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        return [
            {"$match": {"order_date": {"$gte": datetime(2023, 1, 1, tzinfo=UTC)}}},
            {"$unwind": "$line_items"},
            {
                "$lookup": {
                    "from": "products",
                    "localField": "line_items.product_id",
                    "foreignField": "product_id",
                    "as": "product",
                }
            },
            {"$unwind": "$product"},
            {
                "$group": {
                    "_id": "$product.category_id",
                    "revenue": {"$sum": "$line_items.extended_price"},
                    "customers_set": {"$addToSet": "$customer_id"},
                    "order_dates_set": {"$addToSet": "$order_date"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "category_id": "$_id",
                    "revenue": 1,
                    "customer_count": {"$size": "$customers_set"},
                    "order_date_count": {"$size": "$order_dates_set"},
                }
            },
            {"$sort": {"revenue": -1}},
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        # Use COUNT(DISTINCT) for the customer/date aggregations rather than
        # COLLECT — the collected types ride into Python via opaque OBJECT
        # types that don't deserialize cleanly. Counts are equivalent for the
        # equivalence check (Mongo $addToSet emits sets; both engines emit the
        # same cardinality). The 100 MB working set is exercised through the
        # GROUP BY and the line_items unnest, not the COLLECT shape.
        return """
WITH order_lines AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         JSON_VALUE(o.payload, '$.order_date'  RETURNING TIMESTAMP WITH TIME ZONE) AS order_date,
         li.product_id,
         li.extended_price
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id NUMBER PATH '$.product_id',
           extended_price NUMBER PATH '$.extended_price'
         )) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2023-01-01 00:00:00 +00:00'
)
SELECT p.category_id,
       SUM(ol.extended_price) AS revenue,
       COUNT(DISTINCT ol.customer_id) AS customer_count,
       COUNT(DISTINCT ol.order_date)  AS order_date_count
FROM order_lines ol
JOIN products p ON p.product_id = ol.product_id
GROUP BY p.category_id
ORDER BY revenue DESC
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="ws=150MB", parameters={"working_set_mb": 150})
        ws_mb = int(v.parameters["working_set_mb"])
        return [
            Prediction(
                claim=f"working set {ws_mb} MB: Mongo spills when ws >= 100 MB",
                metric="mongo_has_spill",
                operator="==",
                expected_value=ws_mb >= 100,
                confidence="high",
            ),
            Prediction(
                claim=f"working set {ws_mb} MB: ratio rises sharply past 100 MB",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=2.0 if ws_mb >= 150 else 1.2,
                confidence="medium-high",
            ),
        ]
