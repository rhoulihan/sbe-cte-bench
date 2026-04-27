"""Aggregate raw run records into the headline summary CSV.

Walks ``results/raw/*.json``, validates each one against the
:class:`RunRecord` schema, computes the cross-engine ratio, and writes a
flat CSV row per (scenario, variant) — the format expected by
``docs/07-reporting.md``'s headline summary table.

Invalid run records are skipped with a warning to stderr; aggregation
continues. This keeps a single bad file from blocking publication of an
otherwise-good batch.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from sbe_cte_bench.config.run_record import RunRecord

_COLUMNS = (
    "scenario",
    "scenario_title",
    "variant",
    "mongo_median_ms",
    "mongo_p95_ms",
    "mongo_iqr_ms",
    "oracle_median_ms",
    "oracle_p95_ms",
    "oracle_iqr_ms",
    "ratio_mongo_to_oracle",
    "equivalence_match",
    "prediction_pass",
    "run_id",
)


def aggregate_runs(
    raw_dir: Path | str,
    output_csv: Path | str,
    *,
    stderr: TextIO | None = None,
) -> None:
    """Aggregate JSON run records under ``raw_dir`` into a summary CSV."""
    err = stderr if stderr is not None else sys.stderr
    raw = Path(raw_dir)
    out = Path(output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        writer.writeheader()
        for path in sorted(raw.glob("*.json")):
            try:
                record = RunRecord.model_validate_json(path.read_text())
            except (ValidationError, json.JSONDecodeError) as exc:
                err.write(f"warning: skipping invalid run record {path.name}: {exc!r}\n")
                continue
            writer.writerow(_record_to_row(record))


def _record_to_row(record: RunRecord) -> dict[str, str]:
    mongo_med = record.mongo.median_ms
    oracle_med = record.oracle.median_ms
    ratio = mongo_med / oracle_med if oracle_med else float("inf")
    return {
        "scenario": record.scenario,
        "scenario_title": record.scenario_title,
        "variant": json.dumps(record.variant, sort_keys=True),
        "mongo_median_ms": f"{mongo_med:.3f}",
        "mongo_p95_ms": f"{record.mongo.p95_ms:.3f}",
        "mongo_iqr_ms": f"{record.mongo.iqr_ms:.3f}",
        "oracle_median_ms": f"{oracle_med:.3f}",
        "oracle_p95_ms": f"{record.oracle.p95_ms:.3f}",
        "oracle_iqr_ms": f"{record.oracle.iqr_ms:.3f}",
        "ratio_mongo_to_oracle": f"{ratio:.3f}",
        "equivalence_match": str(record.equivalence.match),
        "prediction_pass": str(record.prediction.pass_),
        "run_id": record.run_id,
    }
