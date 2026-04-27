# 05 — Scenarios Index

Fourteen scenarios cover the testable claims from the article plus four research-driven dimensions (planner stability, write path, plan cache, concurrency). Each scenario is a standalone spec with its own data, queries, predictions, and pass/fail criteria.

> The article's storage-format claim (BSON length-prefix scan vs OSON
> hash-indexed navigation, 28×/529× headline) is **out of scope** for this
> bench. Both engines materialize JSON values into a mutable in-memory
> representation before SQL/aggregation evaluation, so per-row dispatch
> dominates the storage-primitive delta at the layer SBE-vs-CTE actually
> measures. That comparison belongs in a dedicated CPU-microbenchmark.

## Scenarios at a glance

| ID | Title | What it measures | Article claim(s) | Status |
|----|-------|------------------|------------------|--------|
| **S01** | Baseline scan + filter + project | Floor performance for both engines on a trivial pipeline. Establishes the noise floor. | (none — calibration) | spec'd |
| **S02** | SBE-prefix best case | Multi-stage pipeline composed entirely of SBE-eligible stages. Best case for MongoDB. | 1, 11 | spec'd |
| **S03** | Boundary tax | Same logical pipeline with `$facet`/`$bucketAuto` placed at varying positions. Measures slope of latency vs boundary position. | 1, 2, 6 | spec'd |
| **S04** | 100 MB stage wall | `$group` and `$sort` working sets that cross the 100 MB per-stage RAM cap. Measures spill cost. | 3 | spec'd |
| **S05** | 16 MiB document cap | `$group` + `$push` accumulator that exceeds 16 MiB per group. Designed to fail on Mongo. | 4 | spec'd |
| **S06** | `$lookup` on sharded foreign | Local + foreign collection sharded; `$lookup` falls back to classic scatter-gather. | 5 | spec'd |
| **S07** | Recursive traversal | Category taxonomy (4-level tree) + product rollup. `$graphLookup` (classic-only) vs Oracle recursive CTE. **Two topology variants**: unsharded and sharded (S06 topology). | 5, 6 | spec'd |
| **S08** | Window functions | `$setWindowFields` after a non-pushable preceding stage vs SQL window function. | 7 | spec'd |
| **S09** | Predicate pushdown / join reordering | Same logical query written in two stage orders. Measures CBO's freedom to reorder vs Mongo's stage-bound semantics. | 8 | spec'd |
| **S10** | Top-N optimization | `$sort` + `$limit` followed by additional stages vs `FETCH FIRST N ROWS ONLY` in CTE. | 9 | spec'd |
| **S12** | Concurrent load | N concurrent workers running a representative scenario. Measures contention behavior, tail latency. | (research dim 3) | spec'd |
| **S13** | Planner stability under cardinality drift | Same query, 10×/100×/1000× data scale. MongoDB FPTP vs Oracle CBO replan. | (research dim 1) | spec'd |
| **S14** | Write path: `$merge` vs `MERGE INTO` | Persisting aggregation results back to a collection/table, with consistency semantics. | (research dim 4) | spec'd |
| **S15** | Plan-cache pollution | 10 K distinct query shapes in a bursty workload. Plan-cache hit rate, recompilation cost, tail latency. | (research dim 5) | spec'd |

## Reading order

S01 first — it calibrates the noise floor and verifies the harness. Then any of S02–S10 in any order; they're independent. S12–S15 require S02–S05 to have produced reasonable results first (they reuse those queries under different conditions).

## Naming conventions

Each scenario is in `docs/scenarios/Sxx-short-name.md`. Inside, sections appear in this canonical order:

1. **Hypothesis** — one paragraph: what we expect to observe and why.
2. **Article claim mapping** — which numbered claim(s) from `00-overview.md` this scenario tests.
3. **Data dependencies** — which scale factor and which extension flags (if any).
4. **Indexes** — pulls from `04-indexes.md`; flags any scenario-specific extras.
5. **Workload — MongoDB** — full pipeline with stage-by-stage commentary.
6. **Workload — Oracle** — full SQL with CTE-by-CTE commentary.
7. **Verification of equivalence** — how we prove the two queries return the same result.
8. **Predictions** — what we *expect* the run to show, with magnitudes and confidence levels.
9. **Pass/fail criteria** — how we decide whether the scenario successfully tested the claim.
10. **Failure modes** — error scenarios, what to record when they happen.
11. **Variations / sweep parameters** — knobs we change across runs (e.g. boundary position, working-set size).

Predictions are the load-bearing element. A scenario that says "MongoDB will be slower, somehow" is too weak to be falsifiable. A scenario that says "MongoDB latency will increase by ≥30% per stage past the boundary, with R² ≥ 0.85 over the 8-position sweep" *is* falsifiable — and useful regardless of whether the prediction holds.

## Per-claim coverage matrix

For each numbered claim from `00-overview.md`, at least one scenario must test it directly:

| Claim | Primary scenario | Secondary scenarios |
|-------|------------------|---------------------|
| 1: SBE prefix only | S02 (best case) | S03 (boundary), S08 (windows) |
| 2: Boundary materialization tax | S03 | S07, S08 |
| 3: 100 MB per-stage cap | S04 | S08 (windows + spill) |
| 4: 16 MiB document cap | S05 | S14 (`$merge` write) |
| 5: Sharded foreign `$lookup` | S06 | S07-sharded (extends to recursion), S14-V14c (extends to writes) |
| 6: Classic-only stages | S03 | S07, S09 |
| 7: Window function eligibility | S08 | (single primary scenario) |
| 8: CBO reordering | S09 | S10, S13 |
| 9: Top-N optimization | S10 | S02 (in passing) |
| 10: Single iterator tree | S02–S10 | (cumulative; not a single scenario) |

The "single iterator tree" claim is unusual: there is no single scenario that proves "Oracle produces a single iterator tree." It's a property *demonstrated* across multiple scenarios — by capturing the explain plan in each one and observing that nested CTEs are inlined and fused into a single execution tree. The reporting in `07-reporting.md` aggregates this evidence into a cross-scenario summary.

The article's OSON-vs-BSON storage-format claim is intentionally not represented above. See the note at the top of this document.
