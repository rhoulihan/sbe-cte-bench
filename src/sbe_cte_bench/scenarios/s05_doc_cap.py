"""S05 — 16 MiB BSON document cap. Designed-failure scenario.

Per docs/scenarios/S05-16mb-doc-cap.md. MongoDB errors with BSONObjectTooLarge;
Oracle succeeds with JSON_ARRAYAGG over CLOB.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S05DocumentCap(ScenarioBase):
    id: ClassVar[str] = "S05"
    title: ClassVar[str] = "16 MiB BSON document cap"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="base", parameters={}),
            Variant(label="rewrite-bucket", parameters={"workaround": "bucket"}),
            Variant(label="out", parameters={"terminal": "out"}),
            Variant(label="merge", parameters={"terminal": "merge"}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        v = variant or Variant(label="base", parameters={})
        terminal = v.parameters.get("terminal")

        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "customer_id": {"$gte": 100001, "$lte": 100020},
                    "order_date": {"$gte": datetime(2024, 1, 1, tzinfo=UTC)},
                }
            },
            {"$unwind": "$line_items"},
            {
                "$group": {
                    "_id": "$customer_id",
                    "total_revenue": {"$sum": "$line_items.extended_price"},
                    "line_items": {
                        "$push": {
                            "product_id": "$line_items.product_id",
                            "quantity": "$line_items.quantity",
                            "extended_price": "$line_items.extended_price",
                            "attrs": "$line_items.attrs",
                        }
                    },
                }
            },
        ]
        if terminal == "out":
            pipeline.append({"$out": "hot_customer_summary"})
        elif terminal == "merge":
            pipeline.append(
                {
                    "$merge": {
                        "into": "hot_customer_summary",
                        "whenMatched": "replace",
                        "whenNotMatched": "insert",
                    }
                }
            )
        return pipeline

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
WITH hot_lines AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         li.product_id,
         li.quantity,
         li.extended_price,
         li.attrs
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id NUMBER PATH '$.product_id',
           quantity NUMBER PATH '$.quantity',
           extended_price NUMBER PATH '$.extended_price',
           attrs CLOB FORMAT JSON PATH '$.attrs'
         )) li
  WHERE JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) BETWEEN 100001 AND 100020
    AND JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2024-01-01 00:00:00 +00:00'
)
SELECT customer_id,
       SUM(extended_price) AS total_revenue,
       JSON_ARRAYAGG(
         JSON_OBJECT(
           'product_id'     VALUE product_id,
           'quantity'       VALUE quantity,
           'extended_price' VALUE extended_price,
           'attrs'          VALUE attrs FORMAT JSON
         )
         ORDER BY product_id
         RETURNING CLOB
       ) AS line_items
FROM hot_lines
GROUP BY customer_id
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        return [
            Prediction(
                claim="Mongo errors with BSONObjectTooLarge on >= 18 of 20 iterations",
                metric="mongo_error_rate",
                operator=">=",
                expected_value=0.9,
                confidence="very high",
            ),
            Prediction(
                claim="Oracle succeeds on 20 of 20 iterations",
                metric="oracle_error_rate",
                operator="==",
                expected_value=0.0,
                confidence="very high",
            ),
        ]
