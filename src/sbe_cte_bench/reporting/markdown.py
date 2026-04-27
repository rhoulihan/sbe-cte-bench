"""Per-scenario markdown writeup generator.

Takes a parsed run record and produces a human-readable markdown report. The
output goes into ``results/processed/scenario-Sxx-<variant>.md`` and is the
primary deliverable a reader sees alongside the JSON.

The format is deliberately spare — tables, headers, no fluff — because the
spec ``docs/07-reporting.md`` already specifies the cross-scenario summary
table; this is the per-scenario detail.
"""

from __future__ import annotations

import json
from typing import Any

from sbe_cte_bench.config.run_record import RunRecord


def render_scenario_writeup(record_dict: dict[str, Any]) -> str:
    """Render a markdown writeup for a single run record."""
    record = RunRecord.model_validate(record_dict)
    verdict = "PASS" if record.prediction.pass_ else "FAIL"
    ratio = (
        record.mongo.median_ms / record.oracle.median_ms
        if record.oracle.median_ms
        else float("inf")
    )

    return _TEMPLATE.format(
        scenario=record.scenario,
        scenario_title=record.scenario_title,
        run_id=record.run_id,
        timestamp=record.timestamp.isoformat(),
        variant=json.dumps(record.variant, sort_keys=True),
        verdict=verdict,
        claim=record.prediction.claim,
        expected=json.dumps(record.prediction.expected, sort_keys=True),
        observed=json.dumps(record.prediction.observed, sort_keys=True),
        mongo_version=record.mongo.version,
        mongo_median=f"{record.mongo.median_ms:.2f}",
        mongo_p95=f"{record.mongo.p95_ms:.2f}",
        mongo_iqr=f"{record.mongo.iqr_ms:.2f}",
        mongo_cv=f"{record.mongo.cv:.3f}",
        oracle_version=record.oracle.version,
        oracle_median=f"{record.oracle.median_ms:.2f}",
        oracle_p95=f"{record.oracle.p95_ms:.2f}",
        oracle_iqr=f"{record.oracle.iqr_ms:.2f}",
        oracle_cv=f"{record.oracle.cv:.3f}",
        ratio=f"{ratio:.2f}",
        equivalence_match=record.equivalence.match,
        row_count_mongo=record.equivalence.row_count_mongo,
        row_count_oracle=record.equivalence.row_count_oracle,
    )


_TEMPLATE = """\
# {scenario} — {scenario_title}

- **Run id:** `{run_id}`
- **Timestamp:** `{timestamp}`
- **Variant:** `{variant}`
- **Verdict:** {verdict}

## Prediction

> **Claim:** {claim}
>
> Expected: `{expected}`
>
> Observed: `{observed}`

## Timing summary

| Engine | Version | Median (ms) | p95 (ms) | IQR (ms) | CV |
|--------|---------|-------------|----------|----------|-----|
| MongoDB | {mongo_version} | {mongo_median} | {mongo_p95} | {mongo_iqr} | {mongo_cv} |
| Oracle | {oracle_version} | {oracle_median} | {oracle_p95} | {oracle_iqr} | {oracle_cv} |

**Ratio (mongo / oracle):** {ratio}

## Equivalence

- Match: `{equivalence_match}`
- Mongo rows: `{row_count_mongo}`
- Oracle rows: `{row_count_oracle}`
"""
