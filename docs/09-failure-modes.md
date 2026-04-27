# 09 — Failure Modes

Some scenarios in this benchmark are designed to drive MongoDB into known architectural limits — not to embarrass the engine, but to verify that the limits are real, reproducible, and quantitatively bounded. This document is the catalog of failure modes the benchmark deliberately probes.

## The five intended failure modes

### F1 — `BSONObjectTooLarge` from `$group` + `$push`

**Triggered by:** S05.

**Mechanism:** A `$group` stage with a `$push` accumulator collects per-group line items. When the accumulator's BSON encoding for any single `_id` group exceeds 16 MiB, the pipeline errors with `BSONObjectTooLarge`.

**Trigger threshold:** ~16 KiB × N items, where item size is dominated by the `attrs` snapshot (~800 bytes typical). At 50 line items per order × 250 K orders per hot customer = 12.5 M line items × 1 KB ≈ 12.5 GiB per customer if naïvely accumulated. The 16 MiB cap is hit at the first ~16 K accumulated items.

**Workarounds documented in the scenario spec:**
- Pre-`$bucket` partitioning by line-item ID range, then per-partition `$group`, then `$unionWith`. Multi-pass; does not preserve atomicity.
- Replace `$push` with `$addToSet` *if* duplicates aren't meaningful (different semantics).
- Two-pass: first `$out` to an intermediate collection at smaller granularity, then `$group` from there.
- Server-param tuning: `internalQueryMaxPushBytes` raised. **Documented but does not actually fix the problem** because the 16 MiB cap is on the resulting BSON document at output, not on the accumulator's working memory. Scenario verifies this empirically.

**Oracle equivalent:** A `LISTAGG` or `JSON_ARRAYAGG` over the same data produces a single CLOB. CLOBs in 26ai are limited to 4 GiB per row. The same scenario completes successfully on Oracle.

### F2 — Per-stage 100 MB working-set spill

**Triggered by:** S04, S08.

**Mechanism:** A `$group` or `$sort` accumulates more than 100 MB of intermediate state. Since 6.0, `allowDiskUseByDefault: true` lets the pipeline complete by spilling to a temp file. Pre-6.0 behavior was a runtime error; post-6.0 behavior is a multi-second slowdown.

**Trigger threshold:** Working set ≥ 100 MB. Concretely: 80 K distinct group keys × ~1.5 KB of accumulator state ≈ 120 MB.

**Recorded:** Per-stage spill counters from `system.profile` (8.1+) — `$group`'s `groupSpills`, `groupSpilledBytes`, `groupSpilledRecords`, `groupSpilledDataStorageSize`. These confirm the spill happened and let us quantify the spill cost.

**Oracle equivalent:** Workarea memory is governed by `PGA_AGGREGATE_TARGET` and per-operator workarea-size policy. A `GROUP BY` exceeding the workarea grant runs in *one-pass* mode (single spill to temp tablespace) or *multi-pass* if larger still. We capture `v$sql_workarea_active` to verify which mode ran. The Oracle equivalent of "spill" is *graceful workarea downgrade* and is generally cheaper because (a) the temp tablespace is a managed segment with allocation reuse, and (b) the workarea size is sized at plan time based on cardinality estimates — not at 100 MB hard-coded.

### F3 — `$lookup` fallback to classic on sharded foreign

**Triggered by:** S06 (with the optional sharded topology).

**Mechanism:** When the foreign collection in a `$lookup` is sharded, the SBE pushdown disqualifier `isAnySecondaryNamespaceAViewOrNotFullyLocal()` returns true. The pipeline falls back to classic-engine execution, which means a per-local-document remote cursor on each shard, scatter-gather, in-memory join.

**Recorded:** The explain plan's first stage will show `$cursor` wrapping a classic stage tree. Per-iteration timing shows latency proportional to `(local docs) × (shard count)` rather than `(local docs)`.

**Oracle equivalent:** A nested CTE doing the same join uses a hash join (or a partition-wise hash join, if both sides are partitioned compatibly). No per-row remote calls. The benchmark on the Oracle side does not have a sharded topology — it runs against a single 26ai Free instance. The architectural point is that *Oracle's join doesn't get worse* when the data is large; MongoDB's does.

### F4 — `$facet` parallel branch memory pressure

**Triggered by:** S03 (specifically the `boundary_position = $facet`-variant).

**Mechanism:** `$facet` runs each sub-pipeline serially (despite the name), and each sub-pipeline carries its own working set. With multiple branches doing `$group`/`$sort` operations, the cumulative pressure on the WiredTiger cache and on the temp file system can produce a long-tail latency that single-branch pipelines don't show.

**Recorded:** Per-branch timings are not separately exposed in `executionStats`; we infer branch cost from the total `$facet` time minus the prefix time. Cache eviction counters from `serverStatus().wiredTiger` show whether `$facet` evicted hot pages used by other clients.

**Oracle equivalent:** `$facet` semantics translate to either (a) multiple SELECT statements over the same dataset and `UNION ALL` of their results, or (b) lateral correlation with `JSON_OBJECT` aggregation. Both are fully optimized by the CBO. The benchmark prefers (a) because it preserves the multi-branch-of-the-same-pipeline shape.

### F5 — SBE→classic boundary at stage `k`

**Triggered by:** S03 (every variant), S07, S08.

**Mechanism:** Pipeline has SBE-eligible stages 1..k-1, then a non-pushable stage at position k. Per `sbe_pushdown.cpp` r8.2.2: "Stop pushing stages down once we hit an incompatible stage." All stages from k onward run in the classic engine, with `DocumentSource::getNext()` per-row materialization.

**Recorded:** `explain.stages[0].$cursor.queryPlanner` exists; `explain.stages[k].$cursor` does not. The boundary stage index is recorded in the run record. Per-stage executionStats compared to the equivalent SBE-prefix-only pipeline produce the boundary-tax delta.

**Oracle equivalent:** No analogue exists. CTEs are inlined into a single query block, the CBO sees the whole tree, and operators stream rows on demand. Scenario S03 measures the *absence* of the boundary by comparing total wall time to the sum of per-operator estimated times — they should be approximately equal in Oracle (no per-operator materialization cost) and substantially different in Mongo at high boundary positions.

## Recording failures

When a scenario *expects* a failure, the run record's `errors` array is **not** an indication of an invalid run. Per `01-methodology.md`:

```yaml
errors:
  - iteration: 7
    code: 17419
    codeName: "BSONObjectTooLarge"
    errmsg: "BSONObj size: 17389042 (0x10A2632) is invalid. Size must be between 0 and 16793600(16MB)"
    expected: true
```

The `expected: true` flag distinguishes intentional failures (S05's whole point) from harness or environment failures (e.g., container OOM-kill). Predictions for failure-mode scenarios are stated as *failure rates*: "MongoDB will error on ≥ 18 of 20 iterations at the configured scale." Predictions like that pass when the failures actually occur.

## Recording near-failures

Some scenarios push close to limits without crossing them. S03 at `boundary_position = 6` is "deep into the classic-engine zone but not at a hard limit." The run record captures latency, spill, and explain — but no errors. The pass/fail criterion in such cases is a *quantitative* one: "Latency at position 6 must be ≥ 3× latency at position 1, otherwise the boundary tax is not the dominant effect and the scenario is no longer measuring what it claimed to measure."

This is the difference between the benchmark observing "MongoDB is slower" (uninformative) and observing "the SBE→classic boundary tax is responsible for at least 65% of the observed slowdown" (mechanism-grounded).

## What never counts as a failure

- **Result-set size** — neither side has a size limit on streaming cursors / fetch results in the scenarios benchmarked here.
- **Memory pressure on the host** — Mongo is cgroup-capped at 3 GB matching ADB Always Free's envelope; if it OOM-kills under normal load (not S04/S05 designed-failure scenarios), the client VM is undersized or a runaway query is leaking memory. On the ADB side, memory pressure shows up as `ORA-04036` / `ORA-04030` errors and the bench captures them. Investigate; do not publish.
- **Statistics staleness** — Oracle stats should be gathered (`DBMS_STATS.GATHER_TABLE_STATS` for `BENCH`) post-load; Mongo plan cache is warmed in the warmup phase. Anything that shows the engine in a "first cold call" state is a harness bug to fix, not a result to publish.
- **Network jitter** — the harness and Mongo are on the same OCI VM; ADB is reached over LAN within the same region (sub-ms latency). If LAN latency to ADB spikes, it's an OCI infrastructure issue.

## Note on `internalQueryForceClassicEngine`

For ablation, S02 and S03 can be re-run with `setParameter: internalQueryForceClassicEngine: true`. This forces every stage through the classic engine, simulating the 7.0.x default behavior the article notes (SERVER-94735). The delta between SBE-on and classic-forced runs is itself a measurement: it quantifies what SBE actually buys you on the SBE-eligible prefix. If the delta is small, that's a finding worth publishing; if it's large, that's also a finding worth publishing.
