"""S05 — designed-failure scenario covering MongoDB's two cliffs.

The architectural claim under test: MongoDB has *two* hard limits that
abort ``$group`` aggregations when intermediates get large. Oracle has
neither.

1. **Per-operator 100 MB memory cap** on ``$push`` / ``$addToSet``. This
   accumulator **cannot spill to disk** (unlike ``$group`` itself, which
   can with ``allowDiskUse``). Hit it and Mongo aborts with
   ``$push used too much memory and cannot spill to disk``.

2. **Per-output-document 16 MiB BSON cap.** If you reshape the query to
   stay under the 100 MB operator limit but any single output document
   still exceeds 16 MiB, Mongo aborts at serialization with
   ``BSONObj size: NN is invalid``.

Oracle's ``JSON_ARRAYAGG`` over ``CLOB`` has neither limit; the same
logical workload returns the full grouped array regardless of size.

Workload: filter to 2024+ orders (~250K orders at SF1), ``$unwind`` line
items into ~1.25M rows, then ``$group``. Variants:

* ``base``: ``$group`` by ``status`` (5 groups). Each per-status
  accumulator ≈ 200 MB → **fails at the 100 MB ``$push`` operator cap**.
* ``out`` / ``merge``: same shape with a ``$out`` / ``$merge`` terminal.
  The accumulator runs before the write, so it hits the **same 100 MB
  operator cap** — terminal stages don't help.
* ``rewrite-bucket``: chunk by (``status``, year-month) so per-bucket
  accumulators stay under 100 MB. Some buckets clear the operator cap
  but **still produce >16 MiB output documents** → fails at the
  per-output-doc cap.

Equivalence MISMATCH on all four variants is *expected* — it's the
finding the bench is designed to surface. Predictions check Mongo error
rate, not equivalence. The caps are the architectural cliff.
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
        # All four variants are designed to fail on Mongo at SF1 — base /
        # out / merge fail at the 100 MB per-operator cap on $push, and
        # rewrite-bucket clears that but fails at the 16 MiB per-output-
        # document cap. Both are architectural cliffs Oracle doesn't have.
        return [
            Prediction(
                claim=(
                    "Mongo aborts on every iteration — either the 100 MB "
                    "per-operator $push cap or the 16 MiB per-output-doc cap"
                ),
                metric="mongo_error_rate",
                operator=">=",
                expected_value=0.9,
                confidence="very high",
            ),
            Prediction(
                claim="Oracle succeeds on every iteration (no per-operator or per-doc cap)",
                metric="oracle_error_rate",
                operator="==",
                expected_value=0.0,
                confidence="very high",
            ),
        ]
