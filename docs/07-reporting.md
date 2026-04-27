# 07 — Reporting

## Output schema

Every scenario produces one JSON file per run, written to `results/raw/Sxx-<variant>-<timestamp>.json`. The schema:

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "timestamp": "ISO 8601",
  "scenario": "S03",
  "scenario_title": "Boundary tax",
  "variant": { "boundary_position": 4, "scale_factor": 1.0 },
  "host": {
    "kernel": "6.8.0-…",
    "cpu_model": "AMD EPYC 7402P",
    "physical_cores": 16,
    "memory_gb": 128,
    "storage": "Samsung 980 PRO 1TB NVMe"
  },
  "mongo": {
    "version": "8.2.2",
    "framework_control": "trySbeEngine",
    "wt_cache_gb": 24,
    "pipeline": [ /* the actual JS pipeline as JSON */ ],
    "explain": { /* full executionStats output */ },
    "spill": { /* per-stage spill counters */ },
    "timings_ms": [ /* 20 values */ ],
    "median_ms": 0.0,
    "p95_ms": 0.0,
    "p99_ms": 0.0,
    "min_ms": 0.0,
    "max_ms": 0.0,
    "iqr_ms": 0.0,
    "cv": 0.0,
    "cpu_user_ms_median": 0.0,
    "peak_rss_mb": 0,
    "csw_voluntary": 0,
    "csw_involuntary": 0,
    "io_read_bytes": 0,
    "io_write_bytes": 0,
    "errors": []
  },
  "oracle": {
    "version": "26.0.0.0",
    "sga_mb": 1200, "pga_mb": 600,
    "sql": "/* the actual SQL */",
    "plan": { /* parsed dbms_xplan output */ },
    "workarea": { "peak_mem_mb": 0, "onepass_executions": 0, "multipass_executions": 0 },
    "statspack": {
      "begin_snap_id": 0,
      "end_snap_id": 0,
      "report_path": "results/raw/Sxx-...-statspack.txt",
      "top_wait_events": [
        { "event": "db file sequential read", "waits": 0, "time_ms": 0, "avg_wait_ms": 0.0 }
      ],
      "load_profile": { "logical_reads_per_s": 0, "physical_reads_per_s": 0, "parses_per_s": 0 }
    },
    "timings_ms": [ /* 20 values */ ],
    "median_ms": 0.0,
    /* … same statistical fields as mongo … */
    "errors": []
  },
  "equivalence": {
    "mongo_hash": "sha256:…",
    "oracle_hash": "sha256:…",
    "match": true,
    "row_count_mongo": 0,
    "row_count_oracle": 0
  },
  "prediction": {
    "claim": "MongoDB latency rises ≥ 30% per stage past the SBE→classic boundary",
    "expected": { "metric": "ratio_mongo_to_oracle", "operator": ">=", "value": 3.5 },
    "observed": { "metric": "ratio_mongo_to_oracle", "value": 0.0 },
    "pass": false
  }
}
```

The `prediction` block is what makes a scenario falsifiable. Every scenario spec in `docs/scenarios/` declares one or more predictions; the harness writes the observed value and the pass/fail boolean to the run record automatically.

## Aggregation

After all runs complete, a `harness/report.py` script (TBD) walks `results/raw/` and produces:

1. **`results/processed/summary.csv`** — one row per (scenario, variant) with median, p95, ratio, and prediction-pass.
2. **`results/processed/scenario-Sxx.md`** — per-scenario writeup. Tables, observations, links to the underlying run JSON.
3. **`results/processed/charts/Sxx-*.svg`** — per-scenario charts. Conventions below.
4. **`results/processed/cross-scenario.md`** — the cumulative claim-11 summary (single iterator tree, observed across all scenarios where applicable).

## Chart conventions

All charts: SVG, 800×500 px, neutral palette. The benchmark is not a marketing artifact and the charts should be readable in print.

- **MongoDB:** dark green `#0e6c3f` (close to MongoDB brand but desaturated).
- **Oracle:** dark red `#9d2235` (close to Oracle brand but desaturated).
- **Background:** `#fafafa`.
- **Grid lines:** `#dddddd`, dashed.
- **Font:** Inter or system-sans. 12pt axis, 10pt tick.

Standard chart types used across scenarios:

### Latency-vs-knob bar/line chart

For S03 (boundary position), S04 (working-set size), S08 (window size), S13 (data scale), the X-axis is the swept parameter and the Y-axis is `median_ms`. Two series — Mongo and Oracle — with p95 error bars. Logarithmic Y when the range exceeds 10×.

### Tail-latency / cumulative distribution

For S12 (concurrent), each engine gets a CDF of per-iteration latencies. X-axis percentile (linear 0–99 or log p50–p99.9), Y-axis latency. Both engines on the same axes. The shape of the tail is the story; means and medians are misleading at concurrency.

### Stacked spill chart

For S04 and S08, a stacked bar where each scenario variant gets a bar showing in-memory time vs spill time vs IO-wait time. MongoDB-only — Oracle's workarea handling does not produce a directly comparable breakdown, but the Oracle bar is shown adjacent for total-time context.

### Plan-cost annotation

For S09 (predicate pushdown), an annotated diagram showing both plans side by side with cost annotations from `dbms_xplan` and `executionStats`. Not auto-generated from the run record — drawn manually based on the plans, included in the per-scenario writeup.

## Cross-scenario summary table

The headline of any benchmark is one summary table that fits on a slide:

| Scenario | What it tests | MongoDB result | Oracle result | Ratio |
|----------|---------------|----------------|---------------|-------|
| S01 | Calibration | 12.3 ms | 11.8 ms | 1.04× |
| S02 | SBE prefix only | 89.4 ms | 64.1 ms | 1.39× |
| S03 (boundary @ 4) | Stage-boundary tax | 142.0 ms | 38.9 ms | **3.65×** |
| S04 (256 MB working set) | 100 MB cap | 8410 ms (spill) | 1820 ms | **4.62×** |
| S05 (24 MiB accumulator) | 16 MiB cap | **error: BSONObjectTooLarge** | 4210 ms | ∞ |
| S06 (sharded foreign) | `$lookup` fallback | 11.4 s | 312 ms | **36.5×** |
| S07 (4-level recursive) | `$graphLookup` classic | 6.83 s | 421 ms | **16.2×** |
| S08 (window after `$facet`) | Window post-boundary | 4.21 s | 188 ms | **22.4×** |
| S09 (anti-pattern stage order) | CBO reordering | 894 ms | 41 ms | **21.8×** |
| S10 (top-50 with downstream) | Top-N composition | 312 ms | 89 ms | 3.50× |
| S12 @ 32 concurrent | Tail latency | p99 = 8.4 s | p99 = 410 ms | **20.5×** |
| S13 (10× scale) | Planner stability | drift ±18% | drift ±2% | (n/a) |
| S14 (100K row $merge) | Write path | 1.84 s | 940 ms | 1.96× |
| S15 (10K query shapes) | Plan cache | hit rate 31% | hit rate 87% | (n/a) |

Numbers in this table are **placeholders**. They illustrate the *expected shape* of the result, not predictions of the actual measured values. Predictions per-scenario are in each scenario spec.

The benchmark passes/fails as a coherent story when the ratios are roughly consistent with the predictions and the *direction* of every effect matches the article. A scenario whose direction reverses (e.g., MongoDB faster than Oracle on S04) is a finding worth investigating, not a failure to suppress.

## Reproducibility manifest

Every published result includes:

- The Git SHA of `sbe-cte-bench` at the run time.
- The Git SHA of the data generator at run time.
- The data hash of the loaded dataset (post-load, computed by hashing the OSON column on Oracle and the BSON dump on Mongo).
- The full container image digests for both database engines.
- The host hardware fingerprint (CPU model, kernel, memory, storage).
- The exact knob settings on both engines.

Publishing a result without this manifest reduces the result to "trust me." We don't publish those.
