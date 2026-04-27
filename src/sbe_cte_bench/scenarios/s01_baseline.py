"""S01 — Baseline scan + filter + project.

Calibration scenario per ``docs/scenarios/S01-baseline.md``. Establishes the
noise floor of the harness; both engines should be within 0.8x-1.3x of each
other.

Mongo pipeline: simple ``$match`` + ``$project`` with computed ``total_amount``
from line_items[].extended_price.

Oracle: equivalent SELECT with a correlated subquery using JSON_TABLE.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S01Baseline(ScenarioBase):
    id: ClassVar[str] = "S01"
    title: ClassVar[str] = "Baseline scan + filter + project"

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        from datetime import datetime

        return [
            {
                "$match": {
                    "status": "delivered",
                    "order_date": {
                        "$gte": datetime(2025, 1, 1, tzinfo=UTC),
                        "$lt": datetime(2025, 4, 1, tzinfo=UTC),
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "order_id": 1,
                    "customer_id": 1,
                    "order_date": 1,
                    "total_amount": {"$sum": "$line_items.extended_price"},
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return """
SELECT
  JSON_VALUE(payload, '$.order_id'    RETURNING NUMBER)                 AS order_id,
  JSON_VALUE(payload, '$.customer_id' RETURNING NUMBER)                 AS customer_id,
  JSON_VALUE(payload, '$.order_date'  RETURNING TIMESTAMP WITH TIME ZONE) AS order_date,
  (SELECT SUM(jt.extended_price)
   FROM JSON_TABLE(o.payload, '$.line_items[*]'
     COLUMNS (extended_price NUMBER PATH '$.extended_price')) jt
  ) AS total_amount
FROM orders_doc o
WHERE JSON_VALUE(payload, '$.status') = 'delivered'
  AND JSON_VALUE(payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE) >= TIMESTAMP '2025-01-01 00:00:00 +00:00'
  AND JSON_VALUE(payload, '$.order_date' RETURNING TIMESTAMP WITH TIME ZONE)  < TIMESTAMP '2025-04-01 00:00:00 +00:00'
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        return [
            Prediction(
                claim="ratio in [0.8, 1.3] (calibration; both engines comparable)",
                metric="ratio_mongo_to_oracle",
                operator="in",
                expected_value=[0.8, 1.3],
                confidence="high",
            ),
            Prediction(
                claim="explain has no $cursor wrapper at any non-zero stage",
                metric="mongo_classic_boundary_at_stage",
                operator="==",
                expected_value=None,
                confidence="high",
            ),
            Prediction(
                claim="oracle plan has no TEMP TABLE TRANSFORMATION",
                metric="oracle_has_materialized_ctes",
                operator="==",
                expected_value=False,
                confidence="high",
            ),
        ]
