"""S05 — 16 MiB BSON document cap. Designed-failure scenario.

The architectural claim under test: MongoDB's per-output-document 16 MiB
cap is a hard ceiling that aborts ``$group`` accumulators (``$push``,
``$addToSet``) when any single result document grows past it. Oracle has
no equivalent per-output cap; ``JSON_ARRAYAGG`` over ``CLOB`` returns the
full grouped array regardless of size.

Workload: filter to 2024+ orders (~250K orders given the SF1 generator),
``$unwind`` line items into ~1.25M rows, then ``$group`` by ``status`` (5
distinct values). Each output document accumulates ~250K line-item
sub-documents — well over 16 MiB.

Variants demonstrate the cliff:

* ``base``: in-memory ``$group`` returning the cursor — Mongo errors with
  ``BSONObjectTooLarge``; Oracle returns 5 rows.
* ``rewrite-bucket``: chunk by (``status``, year-month) so each
  per-output document stays under the cap — Mongo succeeds; Oracle does
  the same logical grouping.
* ``out``: terminate with ``$out``. The collection write enforces the
  same per-document cap, so this also fails on Mongo.
* ``merge``: terminate with ``$merge``. Same per-document constraint;
  same failure.

Equivalence MISMATCH on the failing variants is *expected* — it's the
finding the bench is designed to surface. Predictions check error rate,
not equivalence.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
        v = variant or Variant(label="base", parameters={})
        terminal = v.parameters.get("terminal")
        workaround = v.parameters.get("workaround")

        date_match = {
            "$match": {"order_date": {"$gte": datetime(2024, 1, 1, tzinfo=UTC)}}
        }
        unwind_lines = {"$unwind": "$line_items"}
        push_li = {
            "$push": {
                "product_id": "$line_items.product_id",
                "quantity": "$line_items.quantity",
                "extended_price": "$line_items.extended_price",
                "attrs": "$line_items.attrs",
            }
        }

        if workaround == "bucket":
            # Architectural escape hatch: chunk the group key so each
            # output document stays under 16 MiB. Per-(status, year-month)
            # buckets ≈ 5 × 12 = 60 output docs, ~3 MB each — fits cleanly.
            return [
                date_match,
                unwind_lines,
                {
                    "$group": {
                        "_id": {
                            "status": "$status",
                            "month": {
                                "$dateToString": {
                                    "format": "%Y-%m",
                                    "date": "$order_date",
                                }
                            },
                        },
                        "total_revenue": {"$sum": "$line_items.extended_price"},
                        "line_items": push_li,
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "status": "$_id.status",
                        "month": "$_id.month",
                        "total_revenue": 1,
                        "line_items": 1,
                    }
                },
                {"$sort": {"status": 1, "month": 1}},
            ]

        # Pathological grouping: 5 status values × ~250K orders × ~5 line
        # items × ~800B attrs ≈ ~200 MB per output document. All 5 outputs
        # exceed 16 MiB; Mongo aborts at first.
        pipeline: list[dict[str, Any]] = [
            date_match,
            unwind_lines,
            {
                "$group": {
                    "_id": "$status",
                    "total_revenue": {"$sum": "$line_items.extended_price"},
                    "line_items": push_li,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "status": "$_id",
                    "total_revenue": 1,
                    "line_items": 1,
                }
            },
            {"$sort": {"status": 1}},
        ]
        if terminal == "out":
            pipeline.append({"$out": "hot_status_summary"})
        elif terminal == "merge":
            pipeline.append(
                {
                    "$merge": {
                        "into": "hot_status_summary",
                        "whenMatched": "replace",
                        "whenNotMatched": "insert",
                    }
                }
            )
        return pipeline

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        v = variant or Variant(label="base", parameters={})
        if v.parameters.get("workaround") == "bucket":
            return cls._sql_bucketed()
        return cls._sql_per_status()

    @staticmethod
    def _sql_per_status() -> str:
        return """
WITH order_lines AS (
  SELECT JSON_VALUE(o.payload, '$.status' RETURNING VARCHAR2(16)) AS status,
         li.product_id, li.quantity, li.extended_price, li.attrs
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id     NUMBER PATH '$.product_id',
           quantity       NUMBER PATH '$.quantity',
           extended_price NUMBER PATH '$.extended_price',
           attrs          CLOB FORMAT JSON PATH '$.attrs'
         )) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE)
    >= TIMESTAMP '2024-01-01 00:00:00 +00:00'
)
SELECT status,
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
FROM order_lines
GROUP BY status
ORDER BY status
""".strip()

    @staticmethod
    def _sql_bucketed() -> str:
        return """
WITH order_lines AS (
  SELECT JSON_VALUE(o.payload, '$.status' RETURNING VARCHAR2(16)) AS status,
         TO_CHAR(JSON_VALUE(o.payload, '$.order_date'
                  RETURNING TIMESTAMP WITH TIME ZONE), 'YYYY-MM') AS month,
         li.product_id, li.quantity, li.extended_price, li.attrs
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id     NUMBER PATH '$.product_id',
           quantity       NUMBER PATH '$.quantity',
           extended_price NUMBER PATH '$.extended_price',
           attrs          CLOB FORMAT JSON PATH '$.attrs'
         )) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE)
    >= TIMESTAMP '2024-01-01 00:00:00 +00:00'
)
SELECT status,
       month,
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
FROM order_lines
GROUP BY status, month
ORDER BY status, month
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="base", parameters={})
        if v.parameters.get("workaround") == "bucket":
            # Escape hatch: per-(status, month) chunks fit under 16 MiB;
            # both engines succeed and produce the same result.
            return [
                Prediction(
                    claim="rewrite-bucket: both engines succeed (per-doc < 16 MiB)",
                    metric="mongo_error_rate",
                    operator="==",
                    expected_value=0.0,
                    confidence="very high",
                ),
            ]
        # base / out / merge: per-status accumulator > 16 MiB; Mongo fails.
        return [
            Prediction(
                claim="Mongo errors with BSONObjectTooLarge on every iteration",
                metric="mongo_error_rate",
                operator=">=",
                expected_value=0.9,
                confidence="very high",
            ),
            Prediction(
                claim="Oracle succeeds on every iteration (no per-doc cap)",
                metric="oracle_error_rate",
                operator="==",
                expected_value=0.0,
                confidence="very high",
            ),
        ]
