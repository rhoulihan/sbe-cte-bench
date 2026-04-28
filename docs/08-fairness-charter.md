# 08 — Fairness Charter

A benchmark that doesn't engage seriously with the question of fairness is a polemic with timing data attached. This document is the explicit charter — what we commit to doing to make the comparison fair, where the limits of "fair" are, and what we deliberately do not control for.

## What "fair" means in this benchmark

Fair = **the comparison surfaces real architectural differences without either system being deliberately under-tuned to fit a narrative.**

That definition has two parts:

1. **Real architectural differences.** The benchmark exists to measure what actually differs between MongoDB's stage-bound aggregation pipeline and Oracle's CBO-driven CTE plan. We're not trying to manufacture differences; we're trying to make the inherent differences visible.
2. **Without under-tuning either system.** Both engines get best-effort tuning within their respective architectures. MongoDB gets SBE forced on, the latest server params, parity indexes. Oracle gets stats gathered, parity indexes, no artificial materialization barriers.

If a scenario shows MongoDB slower than Oracle, the scenario must show *why* — via explain plans, spill metrics, and a mechanism explanation. "MongoDB was slower" is a result; "MongoDB crossed the SBE→classic boundary at stage 4 and paid 8.4 ms × N rows of materialization tax" is an *evidence-backed* result.

## Explicit fairness commitments

### Hardware

- **Oracle:** Autonomous Database **Always Free tier** — 1 OCPU (= 2 ECPU), ~3 GB shared SGA, 20 GB storage on shared Exadata-backed infrastructure. Production-grade HA with 99.95% SLA, automatic backups, automatic patching, **$0/month, permanent**. ⚠️ Smart Scan offload and In-Memory column store are **NOT exposed on Always Free** (verified empirically — plans show `TABLE ACCESS FULL` not `STORAGE FULL`). The bench measures Oracle without its biggest performance features. Numbers are conservative.
- **MongoDB:** native install on a paid OCI compute VM (`VM.Standard.E5.Flex` with **2 OCPU / 24 GB**) in the same Availability Domain as the ADB instance. Storage on a paid **Ultra-High-Performance paravirtualized block volume, 120 VPU/GB → ~115K IOPS / 920 MB/s sustained**. mongod itself is `systemd` cgroup-capped to **2 vCPU / 3 GB / 1.5 GB WT cache** so its workload-relevant compute envelope matches ADB Always Free's 1 OCPU tier — but the OS, kernel, page cache, network buffers, harness, and storage layer all benefit from the full VM resources.
- **Network:** client harness on the same VM as mongod (localhost routing — no network hop for client → Mongo). ADB reached via OCI internal network in the same AD — sub-millisecond LAN latency.
- **Cost asymmetry:** ADB Always Free is **$0/month, production-grade**. The MongoDB infrastructure costs roughly **$90–120/month** for the VM + Ultra-High-Performance storage. There is no MongoDB Atlas tier comparable to ADB Always Free — Atlas M0 is explicitly tagged "not for production use" in Atlas's own docs (no HA, no SLA, single-node sandbox). The closest production-tier Mongo equivalent is M30+ at ~$390/month dedicated cluster.
- The bench is therefore **biased toward MongoDB on hardware**: 2× more host CPU, 8× more host RAM, dedicated 920 MB/s local storage, no client network overhead. Despite this, Oracle Always Free wins the vast majority of scenarios by 2–18×. The architectural argument the bench surfaces is that **engine architecture beats hardware throwing** at this class of workload.
- The phenomena under test (boundary tax, 100 MB / 16 MiB caps, sharded fallback, recursive traversal) are largely scale-invariant — SF1 (1M orders, 100K customers, 100K employees) is sufficient to publish architectural findings.

#### Documented exception: S06 sharded topology

S06 (and S14's V14-c variant) require a sharded MongoDB cluster. The user-provided BYOE setup has to add `mongos` + config + multiple shards on the client VM (the included install script provisions a single-node replica set only; sharded scenarios are skipped on that baseline). Two-shard configurations legitimately consume more resources than a single Oracle instance, so we don't try to cap them to the same envelope — the architectural cliff under test (SBE→classic fallback on sharded foreign + scatter-gather per local doc) is independent of resource budget. Giving Mongo 2× the resources doesn't repair the cliff.

S06 and the sharded variants of S07 / S14 are skipped by default on the BYOE setup; they require a sharded Mongo deployment the user provisions separately.

### Indexes

- Parity audit table in `04-indexes.md`. Every index on one side has its analogue on the other, declared per scenario.
- Index-build time is excluded from query timing. Both indexes are built and warm before any timing iteration.
- `EXPLAIN`/`explain()` verifies the expected index is actually used per iteration. If an unexpected `COLLSCAN` or `TABLE ACCESS FULL` appears, the scenario is fixed (typically by re-checking statistics or by a hint).

### Knobs

- MongoDB: `internalQueryFrameworkControl: trySbeEngine` explicitly, `allowDiskUseByDefault: true` (default), all per-stage memory caps at their 100 MB defaults. Per-scenario ablation may raise these — when it does, both engines get equivalent treatment (Oracle PGA target raised proportionally).
- Oracle: stats freshly gathered after load, `OPTIMIZER_USE_FEEDBACK = TRUE`, `RESULT_CACHE_MODE = MANUAL` (no result-cache hits across iterations), no SQL Plan Baselines pinned, no SQL Profiles applied. CBO operates with default cost model.
- Plan cache cleared between scenarios on both sides.

### Queries

- Each scenario specifies a MongoDB pipeline AND an Oracle CTE. Both are reviewed for equivalence — same logical result, same predicates, same projections.
- Equivalence is verified by hashing the canonicalized result set on both sides before timings are accepted as valid (`01-methodology.md`).
- Queries are written to be **idiomatic** for each engine. The Oracle CTE is not deliberately convoluted to handicap Oracle; the MongoDB pipeline is not deliberately written to circumvent SBE. Each query is the natural expression of the scenario's intent in its native dialect.

### Versioning

- MongoDB 8.2.2 (latest stable with full SBE coverage as of v1.0 of the benchmark).
- Oracle Database 26ai Free (latest GA. JSON Duality Views, full OSON support, 26ai-specific JSON optimizer enhancements all available).
- Driver versions pinned in `02-infrastructure.md`.

## What we deliberately do not control for

Some differences are inherent to the systems and would require eliminating distinct features to "control for" them. We don't:

- **MongoDB has no cost-based optimizer; Oracle does.** This is the central architectural difference under test. We don't normalize it away by feeding both engines hand-rolled plans. Where MongoDB's First-Past-the-Post optimizer picks a worse plan than Oracle's CBO, that's the measurement.
- **Oracle has materialized views; MongoDB has materialized views via `$out`/`$merge` collections.** The article concedes both. Scenarios do not pre-materialize either side. Where a scenario *is* a write-back ($merge / MERGE INTO) test, both sides write through the natural mechanism.
- **MongoDB's BSON is length-prefixed; Oracle's OSON is hash-indexed.** This is the engine-storage difference proven elsewhere (DocBench, BSON-OSON bakeoff). It is **out of scope** for this bench: both engines materialize JSON values into a mutable in-memory representation before SQL/aggregation evaluation, so per-row dispatch dominates the storage-primitive delta at the layer the bench actually measures. The article's 28×/529× headline figures are CPU-microbenchmark scale and belong in a dedicated harness.
- **Oracle has Exadata storage offload; the bench host does not.** v1.0 runs on commodity hardware; Smart Scan and Storage Indexes are not exercised. This *understates* Oracle's production performance. Acknowledged. Out of scope for engine-architecture comparison.
- **MongoDB's pipeline is JSON-array natural; Oracle's CTE is SQL natural.** Developer ergonomics are real. Not measurable in milliseconds.
- **Schema flexibility.** MongoDB's appeal-of-freedom is real. The benchmark assumes a fixed schema on both sides — which is what production analytical workloads tend to look like anyway.

## Where we make MongoDB look better than typical production

To preempt critique that the benchmark is rigged against Mongo:

- **Self-hosted, not Atlas.** Atlas tier IOPS throttling would make MongoDB look much worse on every IO-bound scenario. Self-hosted on local SSD/NVMe gives Mongo the benefit of the doubt — the engine is what's measured, not the cloud tier.
- **Single-node replica set.** Replica-set write concern at `w: 1` is the default; we don't add majority-write overhead that wouldn't apply to the read-only scenarios.
- **No driver-side compression.** Snappy/zstd on the wire would penalize the engine that produces larger result sets — that's a wire-format penalty, not an engine-architecture one.
- **Best-of-runs reporting via median + IQR.** No one's reporting a single bad run.
- **Express Path explicitly *not* avoided where it naturally applies.** S01's primary-key point read uses Express Path on Mongo and a unique-index lookup on Oracle. Both fast; both reported. We don't construct queries to defeat Express Path.
- **No Oracle-only features that don't have a Mongo analogue used to give Oracle an edge.** No Exadata Smart Scan (impossible on Free anyway). No Result Cache. No SQL Plan Baselines. No Materialized Views. The CBO operates with default cost model on plain B-tree, function-based, and JSON Search indexes — exactly the toolset MongoDB has analogues for.

## Where we deliberately drive MongoDB to its limits

S04 (100 MB cap), S05 (16 MiB cap), S06 (sharded `$lookup`) are designed to *find* the architectural cliffs. This is not unfair — it is the entire point. The article makes specific claims about specific cliffs; the benchmark reproduces the conditions under which those claims become measurable. If a cliff is at 100 MB, we run a workload at 50 MB *and* 200 MB, so the cliff is visible as a discontinuity.

It would be unfair to:

- Run only the post-cliff workload and present the failure mode without context. (We always run pre-cliff and post-cliff.)
- Use a workload that hits the cliff "naturally" without flagging that the workload was specifically crafted. (We flag every cliff scenario.)
- Generalize a cliff observation beyond the scenario that produced it. ("MongoDB always errors at scale" — wrong. "MongoDB errors with `BSONObjectTooLarge` when a `$group` accumulator with `$push` exceeds 16 MiB on collections with skewed grouping cardinality" — right.)

## Critique invited

Anyone who reads this benchmark and concludes a specific scenario is unfair should:

1. Identify the unfairness (what's the missing index? what's the wrong knob? what's the misuse of the engine?).
2. Propose the corrected scenario.
3. Re-run.

The harness is reproducible by design. The data is byte-stable. The infrastructure is containerized. There is no excuse for a critique that doesn't include a replicated counter-result.

## A note on motivation

The author of this benchmark works at Oracle. The benchmark has an obvious thesis. That does not make it unfair. It makes it transparent.

A fair benchmark with a stated thesis is preferable to an opaque one that claims neutrality. Every benchmark in the database industry has a sponsor and a thesis; the ones that pretend otherwise are the dishonest ones.
