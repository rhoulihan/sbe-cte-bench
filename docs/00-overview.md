# 00 — Overview

## What this benchmark measures

A multi-stage aggregation expressed two ways — as a MongoDB pipeline and as an Oracle nested CTE over `JSON_TABLE` / JSON Duality Views — running against the same data on the same hardware with parity indexes. The metric of interest is *not* "which database is faster overall." It is *where, why, and by how much* the two execution models diverge.

That phrasing matters. A blanket "MongoDB vs Oracle" benchmark would be polemic and uninformative. Both systems handle simple workloads well. The architectural differences only become measurable at specific seams: when a pipeline crosses the SBE→classic boundary, when a `$group` accumulator passes 16 MiB, when `$lookup` fans out to sharded foreign collections, when a window function follows a non-pushable stage. The benchmark is structured to find those seams and quantify them.

## What the benchmark does *not* measure

- **Single-document point lookups.** Both engines handle these well; MongoDB's 8.0 Express Path makes it competitive on `_id` equality and simple unique-index reads. Pipeline architecture is irrelevant here.
- **Bulk insert throughput.** Different write paths, different durability semantics. Out of scope.
- **Replication / HA failover.** Out of scope. The benchmark targets the single-node aggregation engine.
- **Atlas tier elasticity / ADB auto-scaling.** Cloud billing comparisons are in the article, not the benchmark. Mixing them in would muddle architecture with billing.
- **Schema-flexibility ergonomics.** A real argument, but not measurable in milliseconds.

## Eleven testable claims, mapped to scenarios

The article makes eleven claims. Each one maps to at least one scenario in `docs/scenarios/`:

| # | Claim | Scenarios |
|---|-------|-----------|
| 1 | SBE covers a prefix; later stages fall back to classic | S02, S03 |
| 2 | SBE→classic boundary materializes BSON per row per stage | S03 |
| 3 | 100 MB per-stage cap forces disk spill | S04 |
| 4 | 16 MiB BSON cap aborts large `$group` accumulators | S05 |
| 5 | Sharded foreign `$lookup` falls back to classic scatter-gather | S06 |
| 6 | `$facet`, `$bucketAuto`, `$graphLookup`, `$unionWith` are classic-only | S03, S07 |
| 7 | `$setWindowFields` SBE-eligibility depends on prefix | S08 |
| 8 | CBO reorders inlined CTEs; MongoDB cannot reorder across `$facet`/`$lookup` | S09 |
| 9 | Top-N optimization composes differently with downstream stages | S10 |
| 10 | CBO produces a single iterator tree across nested CTEs | S02–S10 (cumulative) |

> Storage-format claims (BSON length-prefix scan vs OSON hash-indexed
> navigation) are deliberately out of scope. Both engines materialize JSON
> values into a mutable in-memory representation before SQL/aggregation
> evaluation, and the per-row dispatch cost dwarfs the storage-primitive
> delta the article cites. The article's 28×/529× headline figures are
> CPU-microbenchmark scale and belong in a separate harness, not in an
> SBE-vs-CTE aggregation comparison.

Three additional scenarios — S12, S13, S14 — cover dimensions the article underweights but that are crucial for a credible result: concurrent-load behavior, planner stability under cardinality drift, and write-path semantics with `$merge` vs `MERGE INTO`.

## What a successful benchmark run looks like

A successful run produces, for every scenario, a JSON record containing:

- The full MongoDB `explain("executionStats")` output for the pipeline.
- The full Oracle `dbms_xplan.display_cursor` output for the CTE.
- Verification that the two queries returned the same logical result set.
- A timing distribution (median, p95, p99, std dev, n=20 after warmup).
- OS-level counters (CPU user, CPU sys, wall clock, peak RSS, page faults, IO read/write bytes).
- Engine-level counters (MongoDB: spill metrics, plan cache hits, working-set evictions; Oracle: PGA peak, sort/hash-area usage, temp segment writes, buffer cache hits).
- A pre-/post-scenario **Statspack diff report** for Oracle, the AWR-equivalent on Free. Captures top wait events, load profile, latch activity, and tablespace IO across the iteration window. Surfaces systemic effects (contention, IO ceilings, latch waits) that per-query instrumentation alone can't.
- A pass/fail verdict against pre-declared *predictions* (each scenario specifies what we expect to observe and at what magnitude).

The predictions are what make this a falsifiable benchmark rather than a confirmation exercise. If a scenario predicts "MongoDB will spill to disk at working-set 128 MB" and it doesn't, the prediction is wrong and gets corrected — or the scenario gets thrown out.

## Reading order for the rest of `docs/`

1. **`01-methodology.md`** — statistical methodology, warmup, percentile reporting, run isolation.
2. **`02-infrastructure.md`** — hardware specs, OS tuning, container topology, NUMA pinning.
3. **`03-data-model.md`** — synthetic schema, scale factors, generator design, RNG seed, byte-stability.
4. **`04-indexes.md`** — index parity strategy, per-scenario index sets.
5. **`05-scenarios-index.md`** — one-line index of all scenarios with status.
6. **`scenarios/S01.md` … `scenarios/S14.md`** — one spec per scenario.
7. **`06-instrumentation.md`** — what to capture during each run.
8. **`07-reporting.md`** — output JSON schema, plot conventions.
9. **`08-fairness-charter.md`** — explicit fairness commitments and their boundaries.
10. **`09-failure-modes.md`** — scenarios that intentionally drive MongoDB into limits.
11. **`10-glossary.md`** — terminology.

The implementation is downstream of this spec. The spec is the contract.
