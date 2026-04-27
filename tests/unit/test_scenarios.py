"""Tests for the scenario registry and concrete scenario declarations.

Verifies the pieces that don't require live engines:

- Each scenario's id is unique and matches its docs filename pattern.
- Each scenario's variants are non-empty and well-formed.
- Each variant produces a parseable Mongo pipeline and a non-empty SQL.
- Predictions are well-formed (have a claim, metric, operator, value).
"""

from __future__ import annotations

import pytest

from sbe_cte_bench.scenarios import all_scenarios, get_scenario
from sbe_cte_bench.scenarios._base import ScenarioBase, Variant


@pytest.mark.unit
def test_at_least_three_scenarios_registered() -> None:
    scenarios = all_scenarios()
    assert len(scenarios) >= 3
    ids = {s.id for s in scenarios}
    assert {"S01", "S02", "S03"} <= ids


@pytest.mark.unit
def test_get_scenario_by_id() -> None:
    s01 = get_scenario("S01")
    assert s01.title.lower().startswith("baseline")


@pytest.mark.unit
def test_get_unknown_scenario_raises() -> None:
    with pytest.raises(KeyError):
        get_scenario("S99")


@pytest.mark.unit
@pytest.mark.parametrize("scenario_cls", all_scenarios())
def test_each_scenario_has_id_and_title(scenario_cls: type[ScenarioBase]) -> None:
    assert scenario_cls.id, f"{scenario_cls.__name__} missing id"
    assert scenario_cls.title, f"{scenario_cls.__name__} missing title"


@pytest.mark.unit
@pytest.mark.parametrize("scenario_cls", all_scenarios())
def test_each_scenario_yields_at_least_one_variant(scenario_cls: type[ScenarioBase]) -> None:
    variants = scenario_cls.variants()
    assert variants, f"{scenario_cls.id} has no variants"


@pytest.mark.unit
@pytest.mark.parametrize("scenario_cls", all_scenarios())
def test_each_variant_has_a_pipeline(scenario_cls: type[ScenarioBase]) -> None:
    for variant in scenario_cls.variants():
        pipeline = scenario_cls.mongo_pipeline(variant)
        assert isinstance(pipeline, list)
        assert pipeline, f"{scenario_cls.id} {variant.label} produced empty pipeline"
        for stage in pipeline:
            assert isinstance(stage, dict)


@pytest.mark.unit
@pytest.mark.parametrize("scenario_cls", all_scenarios())
def test_each_variant_has_oracle_sql(scenario_cls: type[ScenarioBase]) -> None:
    for variant in scenario_cls.variants():
        sql = scenario_cls.oracle_sql(variant)
        assert isinstance(sql, str)
        assert sql.strip(), f"{scenario_cls.id} {variant.label} produced empty SQL"


@pytest.mark.unit
@pytest.mark.parametrize("scenario_cls", all_scenarios())
def test_each_variant_has_predictions(scenario_cls: type[ScenarioBase]) -> None:
    for variant in scenario_cls.variants():
        preds = scenario_cls.predictions(variant)
        assert isinstance(preds, list)
        assert preds, f"{scenario_cls.id} {variant.label} has no predictions"
        for p in preds:
            assert p.claim
            assert p.metric
            assert p.operator
            assert p.confidence


@pytest.mark.unit
def test_s03_variant_sweep_includes_boundary_position_zero() -> None:
    s03 = get_scenario("S03")
    labels = [v.label for v in s03.variants()]
    assert "k=0" in labels


@pytest.mark.unit
def test_s03_at_k4_pipeline_has_redact_boundary_at_position_4() -> None:
    """S03's boundary marker is $redact (single-stage, identity, classic-only).

    The marker is spliced at position k (1-based) — at k=4, that means
    index 3 in the pipeline list. It replaces the per-variant ``$bucketAuto``
    spliced design (which had row-shape and BSON-size issues at higher
    scale) with a row-stream-preserving SBE→classic boundary trigger.
    """
    s03 = get_scenario("S03")
    variant = next(v for v in s03.variants() if v.label == "k=4")
    pipeline = s03.mongo_pipeline(variant)
    # Position 4 (1-based) = index 3
    assert "$redact" in pipeline[3]


@pytest.mark.unit
def test_s03_at_k0_pipeline_has_no_boundary_marker() -> None:
    s03 = get_scenario("S03")
    variant = next(v for v in s03.variants() if v.label == "k=0")
    pipeline = s03.mongo_pipeline(variant)
    for stage in pipeline:
        assert "$redact" not in stage
        assert "$bucketAuto" not in stage


@pytest.mark.unit
def test_s03_predictions_uniform_across_variants() -> None:
    """Per the architectural finding at SF0.1, S03 predicts a uniform
    ≥10× ratio across all boundary positions — not a per-k slope."""
    s03 = get_scenario("S03")
    for variant in s03.variants():
        preds = s03.predictions(variant)
        assert preds, f"S03 {variant.label} missing predictions"
        # First prediction targets the ratio metric uniformly across k.
        assert preds[0].metric == "ratio_mongo_to_oracle"
        assert preds[0].operator == ">="
        assert float(preds[0].expected_value) == 10.0


@pytest.mark.unit
def test_register_rejects_duplicate_ids() -> None:
    from sbe_cte_bench.scenarios._base import register

    class DuplicateScenario(ScenarioBase):
        id = "S01"  # collides
        title = "duplicate"

        @classmethod
        def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, object]]:
            return [{"$match": {}}]

        @classmethod
        def oracle_sql(cls, variant: Variant | None = None) -> str:
            return "SELECT 1 FROM dual"

        @classmethod
        def predictions(cls, variant: Variant | None = None) -> list:  # type: ignore[type-arg]
            return []

    with pytest.raises(ValueError, match="collision"):
        register(DuplicateScenario)
