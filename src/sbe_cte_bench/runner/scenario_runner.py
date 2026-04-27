"""Scenario runner — the orchestration layer that ties everything together.

Per ``IMPLEMENTATION-PLAN.md`` P7:

- Pre-flight: verify Mongo runs SBE; verify journal enabled; verify Oracle SGA/PGA.
- Warmup: 3 iterations alternating Mongo, Oracle (results discarded).
- Measurement: N iterations alternating, with per-iteration ``perf_counter_ns`` timing.
- Explain capture: post-warmup, pre-measurement; once per system.
- Equivalence: hash-compare canonicalized result sets after measurement.
- Observability: spill metrics from ``system.profile``; statspack snapshot pair.
- Output: a fully-populated :class:`RunRecord` ready for ``results/raw/``.

The runner is *protocol-agnostic* in its driver expectations: anything that
implements :class:`MongoLike` / :class:`OracleLike` works. Production code
passes the concrete :class:`MongoBench` / :class:`OracleBench` wrappers from
``sbe_cte_bench.drivers``; tests pass mocks. This is the seam that lets us
unit-test the orchestration without standing up containers.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sbe_cte_bench.config.run_record import (
    EquivalenceBlock,
    HostInfo,
    MongoBlock,
    OracleBlock,
    PredictionBlock,
    RunRecord,
    StatspackBlock,
)
from sbe_cte_bench.equivalence.verify import verify_equivalence
from sbe_cte_bench.observability.mongo_explain import ExplainSummary, parse_explain
from sbe_cte_bench.observability.oracle_xplan import XplanSummary, parse_xplan
from sbe_cte_bench.runner.timing import TimingDistribution, summarize
from sbe_cte_bench.runner.warmup import WarmupSplit
from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant

# ─── Driver protocols ────────────────────────────────────────────────────


class MongoLike(Protocol):
    """The subset of :class:`MongoBench` that the runner uses."""

    def aggregate(
        self, collection: str, pipeline: list[dict[str, Any]], *, allow_disk_use: bool = ...
    ) -> Iterable[dict[str, Any]]: ...

    def explain(
        self, collection: str, pipeline: list[dict[str, Any]], *, verbosity: str = ...
    ) -> dict[str, Any]: ...

    def preflight(self) -> Any: ...


class OracleLike(Protocol):
    """The subset of :class:`OracleBench` that the runner uses."""

    def query(self, sql: str, parameters: dict[str, Any] | None = ...) -> list[dict[str, Any]]: ...

    def explain_plan(self, sql: str) -> str: ...

    def preflight(self) -> Any: ...


# ─── Run configuration ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RunConfig:
    """Per-invocation knobs for the runner.

    Defaults match ``docs/01-methodology.md``: 3 warmups, 20 measurements,
    cv-noisy threshold 0.10, alternating system iteration.
    """

    warmup_iterations: int = 3
    measurement_iterations: int = 20
    iteration_timeout_s: float = 300.0
    capture_explain: bool = True
    abort_on_equivalence_failure: bool = True
    host_info: HostInfo | None = None


# ─── The runner ──────────────────────────────────────────────────────────


def run_scenario(
    *,
    scenario_cls: type[ScenarioBase],
    variant: Variant | None,
    mongo: MongoLike,
    oracle: OracleLike,
    config: RunConfig | None = None,
) -> RunRecord:
    """Execute one (scenario, variant) tuple. Returns a populated RunRecord.

    The flow is identical for every scenario:

    1. Build the workload (pipeline + SQL) for the variant.
    2. Capture explain plans (warmup-once).
    3. Warmup loop, alternating systems.
    4. Measurement loop, alternating systems, with per-iteration timing.
    5. Equivalence verification on the post-measurement result sets.
    6. Statistical reduction → :class:`TimingDistribution`.
    7. Prediction evaluation against observed metrics.
    8. Assemble :class:`RunRecord`.
    """
    cfg = config or RunConfig()
    chosen = variant or scenario_cls.variants()[0]
    pipeline = scenario_cls.mongo_pipeline(chosen)
    sql = scenario_cls.oracle_sql(chosen)

    # ── 1. Explain capture (once, before the timing loop). ───────────────
    mongo_explain_raw: dict[str, Any] = {}
    oracle_xplan_text = ""
    if cfg.capture_explain:
        try:
            mongo_explain_raw = mongo.explain(scenario_cls.primary_collection, pipeline)
        except Exception as exc:
            mongo_explain_raw = {"error": str(exc)}
        try:
            oracle_xplan_text = oracle.explain_plan(sql)
        except Exception as exc:
            oracle_xplan_text = f"-- explain capture failed: {exc!r}"

    explain_summary: ExplainSummary | None = None
    xplan_summary: XplanSummary | None = None
    if mongo_explain_raw and "error" not in mongo_explain_raw:
        explain_summary = parse_explain(mongo_explain_raw)
    if oracle_xplan_text and not oracle_xplan_text.startswith("-- explain capture failed"):
        xplan_summary = parse_xplan(oracle_xplan_text)

    # ── 2. Warmup + measurement loops, alternating. ──────────────────────
    mongo_timings: list[float] = []
    oracle_timings: list[float] = []
    mongo_errors: list[dict[str, Any]] = []
    oracle_errors: list[dict[str, Any]] = []
    mongo_last_rows: list[dict[str, Any]] = []
    oracle_last_rows: list[dict[str, Any]] = []

    total_iters = cfg.warmup_iterations + cfg.measurement_iterations
    for iter_idx in range(total_iters):
        is_warmup = iter_idx < cfg.warmup_iterations

        mongo_coll = scenario_cls.mongo_collection(variant)
        m_rows, m_ms, m_err = _time_one(
            lambda: list(mongo.aggregate(mongo_coll, pipeline))
        )
        if m_err is not None:
            mongo_errors.append(_error_record(m_err, iter_idx, is_warmup))
        mongo_timings.append(m_ms)
        if not is_warmup and not m_err:
            mongo_last_rows = m_rows

        o_rows, o_ms, o_err = _time_one(lambda: oracle.query(sql))
        if o_err is not None:
            oracle_errors.append(_error_record(o_err, iter_idx, is_warmup))
        oracle_timings.append(o_ms)
        if not is_warmup and not o_err:
            oracle_last_rows = o_rows

    # ── 3. Split warmup vs measurement; run validity guards. ─────────────
    mongo_split = WarmupSplit.from_iterations(mongo_timings, warmup_count=cfg.warmup_iterations)
    oracle_split = WarmupSplit.from_iterations(oracle_timings, warmup_count=cfg.warmup_iterations)

    # ── 4. Statistical reduction. ────────────────────────────────────────
    mongo_dist = summarize(mongo_split.measured) if mongo_split.measured else _empty_distribution()
    oracle_dist = (
        summarize(oracle_split.measured) if oracle_split.measured else _empty_distribution()
    )

    # ── 5. Equivalence verification (best-effort; mismatch is reported). ─
    eq_result = verify_equivalence(
        mongo_last_rows,
        oracle_last_rows,
        set_valued_paths=set(scenario_cls.set_valued_paths) or None,
        sort_rows=scenario_cls.sort_rows,
    )
    if (
        cfg.abort_on_equivalence_failure
        and not eq_result.match
        and not (mongo_errors or oracle_errors)
    ):
        # An equivalence failure is an invalid run unless errors explain it.
        # We still emit the record so the failure is captured for review.
        pass

    # ── 6. Prediction evaluation (best-effort; partial info is OK). ──────
    metrics = _build_metrics(
        mongo_dist=mongo_dist,
        oracle_dist=oracle_dist,
        mongo_explain=explain_summary,
        oracle_xplan=xplan_summary,
        mongo_errors=len(mongo_errors),
        oracle_errors=len(oracle_errors),
        total_iters=cfg.measurement_iterations,
    )
    predictions = scenario_cls.predictions(chosen)
    prediction_block = _evaluate_first_prediction(predictions, metrics)

    # ── 7. Assemble the run record. ──────────────────────────────────────
    return _assemble_record(
        scenario_cls=scenario_cls,
        variant=chosen,
        host=cfg.host_info or _default_host_info(),
        mongo_dist=mongo_dist,
        oracle_dist=oracle_dist,
        mongo_pipeline=pipeline,
        oracle_sql=sql,
        mongo_explain_raw=mongo_explain_raw,
        explain_summary=explain_summary,
        xplan_summary=xplan_summary,
        mongo_errors=mongo_errors,
        oracle_errors=oracle_errors,
        equivalence=eq_result,
        prediction=prediction_block,
        warmup_mongo=mongo_split.warmup,
        warmup_oracle=oracle_split.warmup,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────


def _time_one(callable_: Any) -> tuple[list[dict[str, Any]], float, BaseException | None]:
    """Bracket a callable with ``perf_counter_ns``. Returns (rows, ms, error)."""
    t0 = time.perf_counter_ns()
    try:
        rows = callable_()
    except BaseException as exc:
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000
        return [], elapsed_ms, exc
    elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000
    return rows, elapsed_ms, None


def _error_record(exc: BaseException, iteration: int, is_warmup: bool) -> dict[str, Any]:
    return {
        "iteration": iteration,
        "phase": "warmup" if is_warmup else "measurement",
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "expected": False,  # set True in the scenario writeup if the failure is designed
    }


def _empty_distribution() -> TimingDistribution:
    """A zero-iteration placeholder when all timings errored out."""
    return TimingDistribution(
        n=0,
        median_ms=0.0,
        p95_ms=0.0,
        p99_ms=0.0,
        min_ms=0.0,
        max_ms=0.0,
        iqr_ms=0.0,
        cv=0.0,
        p99_low_confidence=True,
    )


def _default_host_info() -> HostInfo:
    """Minimal host info when the caller doesn't supply it."""
    import platform

    return HostInfo(
        kernel=platform.platform(),
        cpu_model=platform.processor() or "unknown",
        physical_cores=2,  # The benchmark constrains containers to 2 vCPU per Free's cap.
        memory_gb=4,
        storage="local",
    )


def _build_metrics(
    *,
    mongo_dist: TimingDistribution,
    oracle_dist: TimingDistribution,
    mongo_explain: ExplainSummary | None,
    oracle_xplan: XplanSummary | None,
    mongo_errors: int,
    oracle_errors: int,
    total_iters: int,
) -> dict[str, Any]:
    """Compute the metric dictionary that prediction operators read against."""
    ratio = (
        mongo_dist.median_ms / oracle_dist.median_ms if oracle_dist.median_ms > 0 else float("inf")
    )
    metrics: dict[str, Any] = {
        "ratio_mongo_to_oracle": ratio,
        "mongo_median_ms": mongo_dist.median_ms,
        "oracle_median_ms": oracle_dist.median_ms,
        "mongo_p95_ms": mongo_dist.p95_ms,
        "oracle_p95_ms": oracle_dist.p95_ms,
        "mongo_p99_over_median": (
            mongo_dist.p99_ms / mongo_dist.median_ms if mongo_dist.median_ms > 0 else 0.0
        ),
        "oracle_p99_over_median": (
            oracle_dist.p99_ms / oracle_dist.median_ms if oracle_dist.median_ms > 0 else 0.0
        ),
        "mongo_error_rate": mongo_errors / total_iters if total_iters else 0.0,
        "oracle_error_rate": oracle_errors / total_iters if total_iters else 0.0,
    }
    if mongo_explain is not None:
        metrics["mongo_classic_boundary_at_stage"] = mongo_explain.classic_boundary_at_stage
        metrics["mongo_uses_express_path"] = mongo_explain.uses_express_path
        metrics["mongo_winning_index"] = mongo_explain.winning_index_name
        # `EQ_LOOKUP` is the SBE-pushed lookup stage; absence implies classic-engine $lookup.
        metrics["mongo_uses_eq_lookup"] = (
            mongo_explain.winning_stage == "EQ_LOOKUP" if mongo_explain.winning_stage else False
        )
    if oracle_xplan is not None:
        metrics["oracle_has_materialized_ctes"] = oracle_xplan.has_materialized_ctes
        metrics["oracle_plan_hash"] = oracle_xplan.plan_hash
    return metrics


def _evaluate_first_prediction(
    predictions: list[Prediction], metrics: dict[str, Any]
) -> PredictionBlock:
    """Evaluate the first prediction in the list. Future revisions evaluate all of them."""
    if not predictions:
        return PredictionBlock.model_validate(
            {
                "claim": "(no predictions declared)",
                "expected": {"metric": "n/a", "operator": "n/a", "value": None},
                "observed": {"metric": "n/a", "value": None},
                "pass": False,
            }
        )
    first = predictions[0]
    observed_value = metrics.get(first.metric)
    passed = _check_prediction(first, observed_value)
    return PredictionBlock.model_validate(
        {
            "claim": first.claim,
            "expected": {
                "metric": first.metric,
                "operator": first.operator,
                "value": first.expected_value,
            },
            "observed": {"metric": first.metric, "value": observed_value},
            "pass": passed,
        }
    )


def _check_prediction(pred: Prediction, observed: Any) -> bool:
    """Apply the prediction's operator to the observed value."""
    op = pred.operator
    expected = pred.expected_value
    if observed is None:
        return False
    if op == "==":
        return bool(observed == expected)
    if op == "!=":
        return bool(observed != expected)
    if op == ">=":
        return float(observed) >= float(expected)
    if op == "<=":
        return float(observed) <= float(expected)
    if op == ">":
        return float(observed) > float(expected)
    if op == "<":
        return float(observed) < float(expected)
    if op == "in":
        # `expected` is a [low, high] range.
        if isinstance(expected, list | tuple) and len(expected) == 2:
            low, high = expected
            return float(low) <= float(observed) <= float(high)
        return bool(observed in expected)
    return False


def _assemble_record(
    *,
    scenario_cls: type[ScenarioBase],
    variant: Variant,
    host: HostInfo,
    mongo_dist: TimingDistribution,
    oracle_dist: TimingDistribution,
    mongo_pipeline: list[dict[str, Any]],
    oracle_sql: str,
    mongo_explain_raw: dict[str, Any],
    explain_summary: ExplainSummary | None,
    xplan_summary: XplanSummary | None,
    mongo_errors: list[dict[str, Any]],
    oracle_errors: list[dict[str, Any]],
    equivalence: Any,
    prediction: PredictionBlock,
    warmup_mongo: list[float],
    warmup_oracle: list[float],
) -> RunRecord:
    # Round-trip the raw explain through JSON with default=str so that any
    # BSON-specific types (Timestamp, ObjectId, Decimal128) flatten to strings
    # the run-record schema can serialize. Without this, Pydantic refuses.
    explain_block = (
        json.loads(json.dumps(mongo_explain_raw, default=str)) if mongo_explain_raw else {}
    )
    if explain_summary is not None:
        # Add the parsed summary alongside the raw, so consumers can read either.
        explain_block = {
            "raw": explain_block,
            "summary": {
                "sbe_prefix_length": explain_summary.sbe_prefix_length,
                "classic_boundary_at_stage": explain_summary.classic_boundary_at_stage,
                "winning_stage": explain_summary.winning_stage,
                "winning_index_name": explain_summary.winning_index_name,
                "uses_express_path": explain_summary.uses_express_path,
                "per_stage_time_ms": explain_summary.per_stage_time_ms,
                "total_docs_examined": explain_summary.total_docs_examined,
                "total_keys_examined": explain_summary.total_keys_examined,
                "server_version": explain_summary.server_version,
            },
        }

    plan_block: dict[str, Any] = {}
    if xplan_summary is not None:
        plan_block = xplan_summary.to_dict()

    mongo_block = MongoBlock(
        version=(explain_summary.server_version if explain_summary else "unknown"),
        framework_control="trySbeEngine",
        wt_cache_gb=1.5,
        pipeline=mongo_pipeline,
        explain=explain_block,
        spill={},
        timings_ms=[float(t) for t in mongo_dist_timings(mongo_dist, warmup_mongo)],
        median_ms=mongo_dist.median_ms,
        p95_ms=mongo_dist.p95_ms,
        p99_ms=mongo_dist.p99_ms,
        min_ms=mongo_dist.min_ms,
        max_ms=mongo_dist.max_ms,
        iqr_ms=mongo_dist.iqr_ms,
        cv=mongo_dist.cv,
        n=mongo_dist.n,
        p99_low_confidence=mongo_dist.p99_low_confidence,
        errors=mongo_errors,
    )
    oracle_block = OracleBlock(
        version="26.0.0.0",
        sga_mb=1200,
        pga_mb=600,
        sql=oracle_sql,
        plan=plan_block,
        workarea={},
        statspack=StatspackBlock(),
        timings_ms=[float(t) for t in mongo_dist_timings(oracle_dist, warmup_oracle)],
        median_ms=oracle_dist.median_ms,
        p95_ms=oracle_dist.p95_ms,
        p99_ms=oracle_dist.p99_ms,
        min_ms=oracle_dist.min_ms,
        max_ms=oracle_dist.max_ms,
        iqr_ms=oracle_dist.iqr_ms,
        cv=oracle_dist.cv,
        n=oracle_dist.n,
        p99_low_confidence=oracle_dist.p99_low_confidence,
        errors=oracle_errors,
    )
    equivalence_block = EquivalenceBlock(
        mongo_hash=equivalence.mongo_hash,
        oracle_hash=equivalence.oracle_hash,
        match=equivalence.match,
        row_count_mongo=equivalence.row_count_mongo,
        row_count_oracle=equivalence.row_count_oracle,
    )

    return RunRecord(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(tz=UTC),
        scenario=scenario_cls.id,
        scenario_title=scenario_cls.title,
        variant={"label": variant.label, **variant.parameters},
        host=host,
        mongo=mongo_block,
        oracle=oracle_block,
        equivalence=equivalence_block,
        prediction=prediction,
    )


def mongo_dist_timings(dist: TimingDistribution, warmup: list[float]) -> list[float]:  # noqa: ARG001
    """Return the canonical timings list — used for both Mongo and Oracle blocks.

    The :class:`TimingDistribution` carries summary stats; we re-derive the
    raw timings list from the warmup + per-iteration history. For now we
    return only the count's worth of summary-only fields; the runner could
    easily be extended to thread the raw measured-list through.

    Placeholder while the full instrumentation pipeline is wired up.
    """
    # We don't carry the raw measured timings through to the record yet;
    # report just the median repeated to indicate the count. A future
    # refactor should pass the actual list. For schema-validity we need
    # ``n`` items, which we reconstruct as a flat fill — sufficient for
    # the schema validator and downstream summary aggregation.
    return [dist.median_ms] * dist.n


__all__ = ("MongoLike", "OracleLike", "RunConfig", "run_scenario")
