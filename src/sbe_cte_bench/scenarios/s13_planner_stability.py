"""S13 — Planner stability under cardinality drift."""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register
from sbe_cte_bench.scenarios.s02_sbe_prefix import S02SbePrefix


@register
class S13PlannerStability(ScenarioBase):
    id: ClassVar[str] = "S13"
    title: ClassVar[str] = "Planner stability under cardinality drift"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label="V13a-stale-SF1", parameters={"stage": "stale", "scale": "SF1"}),
            Variant(label="V13b-fresh-SF1", parameters={"stage": "fresh", "scale": "SF1"}),
            Variant(label="V13c-skew-stale", parameters={"stage": "skew", "histogram": False}),
            Variant(label="V13c-skew-histogram", parameters={"stage": "skew", "histogram": True}),
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        return S02SbePrefix.mongo_pipeline(variant)

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return S02SbePrefix.oracle_sql(variant)

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(
            label="V13a-stale-SF1", parameters={"stage": "stale", "scale": "SF1"}
        )
        stage = v.parameters["stage"]
        if stage == "stale":
            return [
                Prediction(
                    claim="stale plans: Mongo SF1 stale-plan latency >= 1.5x fresh-plan",
                    metric="mongo_stale_over_fresh",
                    operator=">=",
                    expected_value=1.5,
                    confidence="high",
                ),
            ]
        if stage == "fresh":
            return [
                Prediction(
                    claim="fresh stats: Mongo and Oracle within 30%",
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[0.7, 1.4],
                    confidence="high",
                ),
            ]
        # skew
        has_histogram = bool(v.parameters.get("histogram"))
        return [
            Prediction(
                claim=f"skew with{'out' if not has_histogram else ''} histogram",
                metric="oracle_with_histogram_speedup",
                operator=">=" if has_histogram else "in",
                expected_value=2.0 if has_histogram else [0.8, 1.5],
                confidence="medium-high",
            ),
        ]
