"""S15 — Plan-cache pollution under bursty workload."""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S15PlanCache(ScenarioBase):
    id: ClassVar[str] = "S15"
    title: ClassVar[str] = "Plan-cache pollution under bursty workload"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label=f"shapes-{n}", parameters={"n_shapes": n}) for n in (100, 1_000, 10_000)
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        # Representative shape for explain-capture and equivalence; the
        # full plan-cache pollution workload runs many shape variations at
        # iteration-time via concurrent.py.
        return [
            {"$match": {"customer_id": {"$gte": 1, "$lte": 100}}},
            {
                "$project": {
                    "customer_id": 1,
                    "order_revenue": {"$sum": "$line_items.extended_price"},
                }
            },
            {
                "$group": {
                    "_id": "$customer_id",
                    "revenue": {"$sum": "$order_revenue"},
                }
            },
            {"$sort": {"revenue": -1}},
            {"$limit": 100},
            {"$project": {"_id": 0, "customer_id": "$_id", "revenue": 1}},
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        # The actual S15 workload parametrizes per-iteration; this single
        # SQL is the *representative shape* used for explain-capture and
        # equivalence verification. We use literal values matching the
        # Mongo pipeline below so a single execution produces a comparable
        # result set. The full plan-cache-pollution behaviour is exercised
        # by the harness in concurrent.py at runtime.
        return """
SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
       SUM(li.extended_price) AS revenue
FROM orders_doc o,
     JSON_TABLE(o.payload, '$.line_items[*]'
       COLUMNS (extended_price NUMBER PATH '$.extended_price')) li
WHERE JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) BETWEEN 1 AND 100
GROUP BY JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER)
ORDER BY revenue DESC
FETCH FIRST 100 ROWS ONLY
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="shapes-10000", parameters={"n_shapes": 10_000})
        n = int(v.parameters["n_shapes"])
        if n < 1000:
            return [
                Prediction(
                    claim="small shape budget: both engines hit cache",
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[1.0, 2.5],
                    confidence="high",
                ),
            ]
        return [
            Prediction(
                claim=f"{n} shapes: Mongo cache pollution; ratio rises",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=2.0 if n < 5000 else 3.5,
                confidence="medium-high",
            ),
            Prediction(
                claim=f"{n} shapes: Mongo plan cache hit rate < 60%",
                metric="mongo_plan_cache_hit_rate",
                operator="<=",
                expected_value=0.6 if n >= 5000 else 0.8,
                confidence="high",
            ),
        ]
