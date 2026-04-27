# sbe-cte-bench

**A reproducible benchmark framework comparing MongoDB's aggregation pipeline (with the Slot-Based Executor) to Oracle's nested CTEs over `JSON_TABLE` and JSON Duality Views — on identical data, identical hardware, and the same logical query.**

This repository contains the *specification* for the benchmark. Implementation (data generators, harness, drivers, plotting) lives in `harness/` and is built against this spec.

## Why this benchmark exists

There is no published head-to-head comparing MongoDB's aggregation pipeline architecture to a SQL/JSON CTE plan on the same workload, controlled for hardware, indexes, and knobs. Existing JSON benchmarks (DeepBench, NoBench, YCSB-JSON, UCSB) measure CRUD or single-operator latency. The "First Past the Post" paper (arXiv 2409.16544) measures MongoDB's optimizer in isolation. None of them measure what happens when a multi-stage aggregation crosses the SBE→classic boundary, when `$lookup` falls back to scatter-gather on a sharded foreign collection, or when a `$group` accumulator approaches the 16 MiB BSON cap — and they don't compare any of that to the equivalent CTE plan that an Oracle CBO produces.

That's the gap. This is the benchmark for it.

## Thesis under test

The companion article — `articles/sbe-vs-cte-aggregation-tax.md` in the source repo — argues that SBE accelerates the *interior* of a subset of stages but cannot change the stage-bound pipeline model sitting above it. The benchmark turns that into eleven empirically testable claims:

1. SBE accelerates a *prefix* of the pipeline; once a non-pushable stage appears, all subsequent stages run in the classic engine.
2. The SBE→classic boundary materializes a BSON `Document` per row, per remaining stage. The cost is observable as a slope discontinuity vs. boundary position.
3. Per-stage 100 MB working-set cap forces spill-to-disk in MongoDB; Oracle has no per-operator cap.
4. 16 MiB BSON document cap aborts pipelines whose intermediate `_id` accumulator (e.g. `$group` + `$push`) exceeds it; `$out`/`$merge` does not chunk around this.
5. `$lookup` against a sharded foreign collection drops to classic nested-loop with scatter-gather per local doc.
6. `$facet`, `$bucketAuto`, `$graphLookup`, `$unionWith`, `$redact`, `$geoNear`, `$merge`, `$out` remain classic-only as of MongoDB 8.2.2.
7. `$setWindowFields` is SBE-eligible only if its prefix is also SBE-eligible.
8. Stage order in MongoDB matters — the optimizer reorders some predicates but cannot rewrite across `$facet`/`$lookup`/`$bucketAuto`. Oracle's CBO sees a single inlined query block and reorders freely.
9. Top-N (`$sort` + `$limit`) is recognized in both engines but composes differently with downstream stages.
10. OSON's hash-indexed field navigation makes deep `JSON_TABLE` projections O(1); BSON traversal in MongoDB is O(n) at the `$project`/`$match` boundary.
11. The CBO produces a single iterator tree across multiple inlined CTEs; MongoDB cannot produce the equivalent fused plan.

Each claim is mapped to one or more scenarios in `docs/scenarios/`.

## What "fair" means here

A fair benchmark is one that surfaces real architectural differences without either system being under-tuned to fit a narrative. Operationally:

- **Identical hardware AND identical Docker resource limits.** Both engines run as Docker containers with `--cpus="2.0"` and `--memory="4g"` — exactly matching Oracle Database 26ai Free's hard caps (2 CPU threads, 2 GB SGA+PGA combined). MongoDB has no equivalent caps; constraining MongoDB to Oracle Free's budget is a deliberate fairness commitment so the comparison reflects engine architecture rather than headroom. Same SSD, same kernel, same OS tuning, same NUMA pinning. No Atlas. No ADB. Cloud tier-throttled IOPS would mask the architecture we're trying to measure.
- **Identical data.** The same canonical entities are loaded into both systems via deterministic generators with a pinned RNG seed. Documents are byte-stable across runs.
- **Best-effort indexes for both.** Each scenario enumerates a `mongo-idx` and an `oracle-idx` block. Both are reviewed for parity. If a scenario has a clear MongoDB-favoring or Oracle-favoring index, both indexes are built and the comparison is run twice.
- **Best-effort knobs for MongoDB.** SBE is forced on (`internalQueryFrameworkControl: trySbeEngine`) where applicable. Server params controlling per-stage memory caps are documented and (for ablation studies) raised.
- **Equivalent query semantics.** Each scenario's MongoDB pipeline and Oracle CTE produce the same logical result set. This is verified by row-by-row diff before any timing.
- **Strengths acknowledged.** Where MongoDB wins (e.g. JS-native pipeline construction, single-shard point reads via Express Path), the scenario records that and moves on. The benchmark is not a polemic.

The full fairness charter — including what we deliberately do *not* control for, and why — is in `docs/08-fairness-charter.md`.

## Repository layout

```
sbe-cte-bench/
├── README.md                  ← you are here
├── docs/
│   ├── 00-overview.md         ← what we're measuring and why it matters
│   ├── 01-methodology.md      ← statistical methodology, runs, percentiles
│   ├── 02-infrastructure.md   ← hardware, OS, container, isolation
│   ├── 03-data-model.md       ← synthetic schema, generators, scale factors
│   ├── 04-indexes.md          ← index parity strategy
│   ├── 05-scenarios-index.md  ← all scenarios, one-line summary each
│   ├── 06-instrumentation.md  ← explain plans, OS counters, spill metrics
│   ├── 07-reporting.md        ← output JSON schema, plotting conventions
│   ├── 08-fairness-charter.md ← what we explicitly do to be fair
│   ├── 09-failure-modes.md    ← scenarios designed to make MongoDB error out
│   ├── 10-glossary.md         ← terminology reference
│   └── scenarios/             ← S01–S15, one spec per scenario
├── harness/                   ← driver code (TBD; built against this spec)
├── data/                      ← generator output (gitignored)
└── results/                   ← run output (gitignored)
```

## Status

**Specification: drafted.** Implementation: not started.

The spec is the load-bearing artifact. Anyone who reads `docs/` should be able to build a working benchmark harness without further conversation — and any harness that conforms to the spec should produce comparable results.

## Pinned versions for v1.0 of the benchmark

| Component | Version | Notes |
|-----------|---------|-------|
| MongoDB | 8.x (Community, 8.2.2 minimum) | Official Docker image. SBE on by default. Resource-limited via Docker `--cpus="2.0"` and `--memory="4g"` to match Oracle Free's hard limits. |
| Oracle Database | 26ai Free | Official Docker image (`container-registry.oracle.com/database/free:26ai`). Free edition has hard limits: **12 GB user data per PDB, 2 GB SGA+PGA combined, 2 CPU threads, 1 PDB.** The benchmark sizes itself within these caps so MongoDB and Oracle run on identical resource budgets. |
| Host OS | Ubuntu 24.04 LTS | Kernel 6.8+. cgroup v2. |
| MongoDB driver | `pymongo` 4.10+ | Synchronous; no motor for primary measurements. |
| Oracle driver | `python-oracledb` 2.4+ | Thin mode; no Oracle Client install. |
| Harness language | Python 3.12 | Single language so the timing loop is identical. |
| Plotting | Matplotlib + Seaborn | All charts SVG. |

Any deviation from these versions invalidates the result for v1.0. Future revisions of the benchmark may bump them.

### Why match MongoDB to Oracle Free's caps

Oracle Free's caps are the floor of the comparison; MongoDB has no equivalent caps. To make the comparison fair, we **deliberately constrain MongoDB to the same resource budget Oracle Free is forced to operate within** — 2 CPUs, 2 GB engine RAM, 4 GB container memory. This isolates engine architecture from headroom. A future EE-vs-Atlas-M-tier benchmark at large scale is interesting; this one is about whether SBE's pipeline architecture beats CBO-driven nested CTEs *at fixed resource budget*.

The benchmark therefore runs on any laptop with Docker — no specialized hardware, no cloud spend, no licensing. Reproducibility is the point.

## License

To be decided. Default assumption: Apache 2.0, but the article author's preference governs.

## Citing this benchmark

This benchmark is the supporting evidence for the article *"Faster Slots, Same Stages — Why MongoDB's Slot-Based Executor Doesn't Rescue the Aggregation Pipeline"* by Rick Houlihan. When citing results from this repository, link to both the article and the specific commit hash of `sbe-cte-bench` that produced the result.
