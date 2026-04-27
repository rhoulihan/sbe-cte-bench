"""Tests for the scenario_runner orchestration layer.

These tests pass mock drivers (no Docker, no live engines) so the runner's
end-to-end behaviour — alternating iteration, equivalence check, prediction
evaluation, run-record assembly — is exercised in isolation.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from sbe_cte_bench.config.run_record import RunRecord
from sbe_cte_bench.runner.scenario_runner import RunConfig, run_scenario
from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant


class _StubScenario(ScenarioBase):
    id: ClassVar[str] = "TEST"
    title: ClassVar[str] = "Stub for runner tests"

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        return [{"$match": {"x": 1}}]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        return "SELECT 1 AS x FROM dual"

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        return [
            Prediction(
                claim="ratio in [0.8, 1.3]",
                metric="ratio_mongo_to_oracle",
                operator="in",
                expected_value=[0.8, 1.3],
                confidence="high",
            )
        ]


class _MongoMock:
    """Driver mock that returns deterministic rows + sleeps a configurable amount."""

    def __init__(self, rows: list[dict[str, Any]], sleep_ms: float = 0.0) -> None:
        self._rows = rows
        self._sleep_ms = sleep_ms
        self._call_count = 0

    def aggregate(
        self, collection: str, pipeline: list[dict[str, Any]], *, allow_disk_use: bool = True
    ) -> list[dict[str, Any]]:
        import time

        self._call_count += 1
        if self._sleep_ms:
            time.sleep(self._sleep_ms / 1000.0)
        return list(self._rows)

    def explain(
        self, collection: str, pipeline: list[dict[str, Any]], *, verbosity: str = "executionStats"
    ) -> dict[str, Any]:
        return {
            "explainVersion": "2",
            "stages": [
                {
                    "$cursor": {
                        "queryPlanner": {
                            "winningPlan": {
                                "queryPlan": {"stage": "IXSCAN", "indexName": "ix_test"}
                            }
                        },
                        "executionStats": {
                            "totalDocsExamined": 1,
                            "totalKeysExamined": 1,
                            "nReturned": 1,
                            "executionTimeMillisEstimate": 1,
                        },
                    }
                }
            ],
            "serverInfo": {"version": "8.2.2"},
        }

    def preflight(self) -> object:
        return None


class _OracleMock:
    def __init__(self, rows: list[dict[str, Any]], sleep_ms: float = 0.0) -> None:
        self._rows = rows
        self._sleep_ms = sleep_ms
        self._call_count = 0

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        import time

        self._call_count += 1
        if self._sleep_ms:
            time.sleep(self._sleep_ms / 1000.0)
        return list(self._rows)

    def explain_plan(self, sql: str) -> str:
        return (
            "Plan hash value: 1234567890\n"
            "----------------------------------------\n"
            "| Id  | Operation        | Name | Rows |\n"
            "----------------------------------------\n"
            "|   0 | SELECT STATEMENT |      |    1 |\n"
            "|   1 |  TABLE ACCESS BY INDEX ROWID| T  |    1 |\n"
            "----------------------------------------\n"
        )

    def preflight(self) -> object:
        return None


@pytest.fixture
def cfg_fast() -> RunConfig:
    """Tiny iteration counts for fast unit tests."""
    return RunConfig(warmup_iterations=1, measurement_iterations=3, capture_explain=True)


@pytest.mark.unit
def test_run_scenario_returns_valid_run_record(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    assert isinstance(record, RunRecord)
    assert record.scenario == "TEST"


@pytest.mark.unit
def test_run_scenario_records_iteration_counts(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    # measurement_iterations=3 should yield n=3 in the timing distribution
    assert record.mongo.n == 3
    assert record.oracle.n == 3


@pytest.mark.unit
def test_run_scenario_alternates_calls(cfg_fast: RunConfig) -> None:
    """The runner alternates Mongo and Oracle — each is called the same number of times.

    1 warmup + 3 measurement = 4 calls per system.
    """
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    assert mongo._call_count == 4  # 1 warmup + 3 measurement
    assert oracle._call_count == 4


@pytest.mark.unit
def test_run_scenario_equivalence_match_when_rows_identical(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}, {"x": 2}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    assert record.equivalence.match is True


@pytest.mark.unit
def test_run_scenario_equivalence_fails_when_rows_differ(cfg_fast: RunConfig) -> None:
    mongo = _MongoMock([{"x": 1}])
    oracle = _OracleMock([{"x": 2}])
    record = run_scenario(
        scenario_cls=_StubScenario,
        variant=None,
        mongo=mongo,
        oracle=oracle,
        config=cfg_fast,
    )
    assert record.equivalence.match is False


@pytest.mark.unit
def test_run_scenario_evaluates_first_prediction(cfg_fast: RunConfig) -> None:
    """Predictions are exercised — the result is captured in the run record."""
    rows = [{"x": 1}]
    # Both engines run at the same speed; ratio should be ~1, which falls in [0.8, 1.3].
    mongo = _MongoMock(rows, sleep_ms=10.0)
    oracle = _OracleMock(rows, sleep_ms=10.0)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    # The mock-induced ratios are noisy. We assert the prediction was evaluated.
    assert record.prediction.claim == "ratio in [0.8, 1.3]"
    assert record.prediction.observed["metric"] == "ratio_mongo_to_oracle"


@pytest.mark.unit
def test_run_scenario_captures_explain(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    # The explain field carries both the raw and summary blocks.
    assert "summary" in record.mongo.explain
    summary = record.mongo.explain["summary"]
    assert summary["server_version"] == "8.2.2"
    assert summary["winning_index_name"] == "ix_test"


@pytest.mark.unit
def test_run_scenario_captures_oracle_plan(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    assert record.oracle.plan["plan_hash"] == 1234567890


@pytest.mark.unit
def test_run_scenario_records_errors_when_query_fails(cfg_fast: RunConfig) -> None:
    """A failing driver call is captured in the errors list, not raised."""

    class _ErroringMongo(_MongoMock):
        def aggregate(
            self, collection: str, pipeline: list[dict[str, Any]], *, allow_disk_use: bool = True
        ) -> list[dict[str, Any]]:
            self._call_count += 1
            raise RuntimeError("simulated mongo failure")

    mongo = _ErroringMongo([])
    oracle = _OracleMock([{"x": 1}])
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    assert record.mongo.errors
    assert record.mongo.errors[0]["type"] == "RuntimeError"


@pytest.mark.unit
def test_run_scenario_record_serializes_to_json(cfg_fast: RunConfig) -> None:
    """The output is round-trippable through Pydantic JSON serialization."""
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg_fast
    )
    payload = record.model_dump_json(by_alias=True)
    assert "TEST" in payload
    re_parsed = RunRecord.model_validate_json(payload)
    assert re_parsed.scenario == "TEST"


@pytest.mark.unit
def test_run_scenario_disable_explain_skips_capture(cfg_fast: RunConfig) -> None:
    rows = [{"x": 1}]
    mongo = _MongoMock(rows)
    oracle = _OracleMock(rows)
    cfg = RunConfig(
        warmup_iterations=cfg_fast.warmup_iterations,
        measurement_iterations=cfg_fast.measurement_iterations,
        capture_explain=False,
    )
    record = run_scenario(
        scenario_cls=_StubScenario, variant=None, mongo=mongo, oracle=oracle, config=cfg
    )
    # No explain captured.
    assert record.mongo.explain == {}
    assert record.oracle.plan == {}
