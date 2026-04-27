"""S08 — $setWindowFields after a non-pushable stage."""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S08WindowFunctions(ScenarioBase):
    id: ClassVar[str] = "S08"
    title: ClassVar[str] = "Window functions after a non-pushable stage"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="A-clean-prefix", parameters={"variant": "A"}),
            Variant(label="B-facet", parameters={"variant": "B-facet"}),
            Variant(label="B-bucketAuto", parameters={"variant": "B-bucketAuto"}),
            Variant(label="B-graphLookup", parameters={"variant": "B-graphLookup"}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        v = variant or Variant(label="A-clean-prefix", parameters={"variant": "A"})
        kind = v.parameters["variant"]

        recent = {"$match": {"order_date": {"$gte": datetime(2024, 1, 1, tzinfo=UTC)}}}
        # Pre-project revenue per order via inner $sum-on-array; then group.
        project_revenue = {
            "$project": {
                "customer_id": 1,
                "order_date": 1,
                "revenue": {"$sum": "$line_items.extended_price"},
            }
        }
        group = {
            "$group": {
                "_id": {"customer_id": "$customer_id", "date": "$order_date"},
                "revenue": {"$sum": "$revenue"},
            }
        }
        lookup = {
            "$lookup": {
                "from": "customers",
                "localField": "_id.customer_id",
                "foreignField": "customer_id",
                "as": "c",
            }
        }
        unwind_c = {"$unwind": "$c"}
        windowed = {
            "$setWindowFields": {
                "partitionBy": "$c.region_id",
                "sortBy": {"_id.date": 1},
                "output": {
                    "rolling_30d_avg": {
                        "$avg": "$revenue",
                        "window": {"range": [-30, 0], "unit": "day"},
                    }
                },
            }
        }
        # Reshape to match Oracle's flat row shape: customer_id, order_date,
        # revenue, region_id, rolling_30d_avg. Round revenue/avg to cents
        # so float-precision drift across millions of operations doesn't
        # break equivalence — Mongo and Oracle use different summation
        # orders for window-function averages, producing values that agree
        # to ~12 digits but diverge in the last decimal places.
        flatten = {
            "$project": {
                "_id": 0,
                "customer_id": "$_id.customer_id",
                "order_date": "$_id.date",
                "revenue": {"$round": ["$revenue", 2]},
                "region_id": "$c.region_id",
                "rolling_30d_avg": {"$round": ["$rolling_30d_avg", 2]},
            }
        }

        if kind == "A":
            return [recent, project_revenue, group, lookup, unwind_c, windowed, flatten]

        # Variant B-*: insert a non-pushable stage in the prefix.
        if kind == "B-facet":
            non_pushable: list[dict[str, Any]] = [
                {"$facet": {"revenue": [project_revenue, group]}},
                {"$unwind": "$revenue"},
                {"$replaceRoot": {"newRoot": "$revenue"}},
            ]
        elif kind == "B-bucketAuto":
            non_pushable = [
                project_revenue,
                group,
                {"$bucketAuto": {"groupBy": "$_id.customer_id", "buckets": 100}},
                # $bucketAuto changes shape; subsequent stages won't match Oracle.
                # Variant B-bucketAuto's value is the boundary-tax measurement;
                # we accept the equivalence MISMATCH here.
            ]
        else:  # B-graphLookup — no-op self-graphLookup as a classic-only stage
            non_pushable = [
                project_revenue,
                group,
                {
                    "$graphLookup": {
                        "from": "customers",
                        "startWith": "$_id.customer_id",
                        "connectFromField": "customer_id",
                        "connectToField": "customer_id",
                        "as": "self_ref",
                        "maxDepth": 0,
                    }
                },
            ]

        return [recent, *non_pushable, lookup, unwind_c, windowed, flatten]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
WITH daily_customer_revenue AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         JSON_VALUE(o.payload, '$.order_date'  RETURNING TIMESTAMP WITH TIME ZONE) AS order_date,
         (SELECT SUM(li.extended_price)
          FROM JSON_TABLE(o.payload, '$.line_items[*]'
            COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-01-01 00:00:00 +00:00'
),
joined AS (
  SELECT d.customer_id, d.order_date, d.revenue, c.region_id
  FROM daily_customer_revenue d
  JOIN customers c ON c.customer_id = d.customer_id
)
SELECT customer_id, order_date, ROUND(revenue, 2) AS revenue, region_id,
       ROUND(
         AVG(revenue) OVER (
           PARTITION BY region_id
           ORDER BY order_date
           RANGE BETWEEN INTERVAL '30' DAY PRECEDING AND CURRENT ROW
         ),
         2
       ) AS rolling_30d_avg
FROM joined
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="A-clean-prefix", parameters={"variant": "A"})
        kind = v.parameters["variant"]
        if kind == "A":
            return [
                Prediction(
                    claim="clean SBE prefix: ratio in [1.2, 2.5]",
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[1.2, 2.5],
                    confidence="high",
                ),
            ]
        return [
            Prediction(
                claim="upstream non-pushable -> $setWindowFields runs classic; ratio >= 3x",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=3.0,
                confidence="high",
            ),
        ]
