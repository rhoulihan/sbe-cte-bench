"""S04 — 100 MB per-stage memory cap. Per docs/scenarios/S04-100mb-stage-wall.md.

The ``working_set_mb`` knob scales the Mongo ``$group`` accumulator state by
adding progressively more ``$addToSet`` (and at the top tier, ``$push``)
accumulators. Each tier projects the accumulated set down to a count, so the
final output schema matches Oracle's ``COUNT(DISTINCT ...)`` columns. Mongo
materializes the full set in ``$group`` state before the projection sees
it — that's what crosses the 100 MB per-stage cap. Oracle's hash aggregate
streams ``COUNT(DISTINCT)`` and never accumulates the underlying values.
"""

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

    @staticmethod
    def _ws(variant: Variant | None) -> int:
        if variant is None:
            return 25
        return int(variant.parameters["working_set_mb"])

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        ws = cls._ws(variant)

        # Accumulators stack additively as ws climbs. Each $addToSet materializes
        # the full distinct set in $group state before $project squashes it to
        # a count. That's the memory pressure 100 MB cap is designed to test.
        group_accum: dict[str, Any] = {
            "revenue": {"$sum": "$line_items.extended_price"},
            "customers_set": {"$addToSet": "$customer_id"},
        }
        if ws >= 50:
            group_accum["order_dates_set"] = {"$addToSet": "$order_date"}
        if ws >= 75:
            group_accum["product_ids_set"] = {"$addToSet": "$line_items.product_id"}
        if ws >= 100:
            group_accum["sku_set"] = {"$addToSet": "$product.sku"}
        if ws >= 150:
            group_accum["name_set"] = {"$addToSet": "$product.name"}
        if ws >= 200:
            group_accum["status_set"] = {"$addToSet": "$status"}
        if ws >= 250:
            # $push the whole line_item subdoc — heaviest accumulator.
            group_accum["line_items_pushed"] = {"$push": "$line_items"}

        project: dict[str, Any] = {
            "_id": 0,
            "category_id": "$_id",
            "revenue": 1,
            "customer_count": {"$size": "$customers_set"},
        }
        if ws >= 50:
            project["order_date_count"] = {"$size": "$order_dates_set"}
        if ws >= 75:
            project["product_count"] = {"$size": "$product_ids_set"}
        if ws >= 100:
            project["sku_count"] = {"$size": "$sku_set"}
        if ws >= 150:
            project["name_count"] = {"$size": "$name_set"}
        if ws >= 200:
            project["status_count"] = {"$size": "$status_set"}
        if ws >= 250:
            project["lineitem_count"] = {"$size": "$line_items_pushed"}

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
            {"$group": {"_id": "$product.category_id", **group_accum}},
            {"$project": project},
            {"$sort": {"revenue": -1}},
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        ws = cls._ws(variant)

        # Oracle uses streaming COUNT(DISTINCT) — no per-group set materialization.
        # Output column set must match Mongo's $project for equivalence verification.
        selects: list[str] = [
            "p.category_id",
            "SUM(ol.extended_price) AS revenue",
            "COUNT(DISTINCT ol.customer_id) AS customer_count",
        ]
        if ws >= 50:
            selects.append("COUNT(DISTINCT ol.order_date) AS order_date_count")
        if ws >= 75:
            selects.append("COUNT(DISTINCT ol.product_id) AS product_count")
        if ws >= 100:
            selects.append("COUNT(DISTINCT p.sku) AS sku_count")
        if ws >= 150:
            selects.append("COUNT(DISTINCT p.name) AS name_count")
        if ws >= 200:
            selects.append("COUNT(DISTINCT ol.status) AS status_count")
        if ws >= 250:
            selects.append("COUNT(*) AS lineitem_count")

        select_clause = ",\n       ".join(selects)

        return f"""
WITH order_lines AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         JSON_VALUE(o.payload, '$.order_date'  RETURNING TIMESTAMP WITH TIME ZONE) AS order_date,
         JSON_VALUE(o.payload, '$.status'      RETURNING VARCHAR2(16)) AS status,
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
SELECT {select_clause}
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
