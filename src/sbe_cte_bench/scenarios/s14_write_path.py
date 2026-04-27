"""S14 — Write path: $merge vs MERGE INTO."""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S14WritePath(ScenarioBase):
    id: ClassVar[str] = "S14"
    title: ClassVar[str] = "Write path: $merge vs MERGE INTO"
    sort_rows: ClassVar[bool] = (
        False  # write workloads are state-mutation; equivalence is checked on the target collection state
    )

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="V14a-routine", parameters={"variant": "a"}),
            Variant(label="V14b-txn-consistency", parameters={"variant": "b"}),
            Variant(
                label="V14c-sharded-target", parameters={"variant": "c", "topology": "sharded"}
            ),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        # All variants: aggregate then merge to customer_summary.
        return [
            {"$match": {"order_date": {"$gte": datetime(2024, 8, 1, tzinfo=UTC)}}},
            {
                "$group": {
                    "_id": "$customer_id",
                    "revenue": {"$sum": "$line_items.extended_price"},
                }
            },
            {
                "$merge": {
                    "into": "customer_summary",
                    "on": "_id",
                    "whenMatched": "replace",
                    "whenNotMatched": "insert",
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
MERGE INTO customer_summary tgt
USING (
  WITH revenue_by_customer AS (
    SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
           (SELECT SUM(li.extended_price)
            FROM JSON_TABLE(o.payload, '$.line_items[*]'
              COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
    FROM orders_doc o
    WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-08-01 00:00:00 +00:00'
  )
  SELECT customer_id, SUM(revenue) AS revenue
  FROM revenue_by_customer
  GROUP BY customer_id
) src
ON (tgt.customer_id = src.customer_id)
WHEN MATCHED THEN UPDATE SET tgt.revenue = src.revenue
WHEN NOT MATCHED THEN INSERT (customer_id, revenue) VALUES (src.customer_id, src.revenue)
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="V14a-routine", parameters={"variant": "a"})
        kind = v.parameters["variant"]
        if kind == "a":
            return [
                Prediction(
                    claim="routine batch upsert: ratio in [1.5, 3]",
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[1.5, 3.0],
                    confidence="high",
                ),
            ]
        if kind == "b":
            return [
                Prediction(
                    claim="txn consistency forces 2-stage Mongo workaround: ratio >= 2x",
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=2.5,
                    confidence="high",
                ),
            ]
        return [
            Prediction(
                claim="sharded target: ratio >= 5x (scatter cost on Mongo $merge)",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=5.0,
                confidence="high",
            ),
        ]
