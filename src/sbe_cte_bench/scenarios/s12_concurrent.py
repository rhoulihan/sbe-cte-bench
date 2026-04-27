"""S12 — Concurrent load. Reuses S02's workload at varying worker counts."""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register
from sbe_cte_bench.scenarios.s02_sbe_prefix import S02SbePrefix


@register
class S12Concurrent(ScenarioBase):
    id: ClassVar[str] = "S12"
    title: ClassVar[str] = "Concurrent load (S02 workload, varied N)"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [Variant(label=f"N={n}", parameters={"workers": n}) for n in (1, 2, 4, 8)]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        return S02SbePrefix.mongo_pipeline(variant)

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return S02SbePrefix.oracle_sql(variant)

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="N=8", parameters={"workers": 8})
        n = int(v.parameters["workers"])
        # Mongo p99 grows super-linearly with N; Oracle sub-linearly.
        return [
            Prediction(
                claim=f"N={n}: Mongo p99/median ratio rises with N",
                metric="mongo_p99_over_median",
                operator=">=",
                expected_value=1.5 if n <= 2 else (2.5 if n <= 4 else 4.0),
                confidence="medium-high",
            ),
            Prediction(
                claim=f"N={n}: Oracle p99/median ratio rises gracefully",
                metric="oracle_p99_over_median",
                operator="<=",
                expected_value=2.0 if n <= 4 else 3.0,
                confidence="medium-high",
            ),
        ]
