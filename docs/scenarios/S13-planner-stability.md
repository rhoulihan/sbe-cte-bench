# S13 — Planner stability under cardinality drift

## Hypothesis

MongoDB's First-Past-the-Post (FPTP) optimizer (per arXiv 2409.16544) selects plans by partial execution: the candidate that returns its first batch fastest wins. This works well at static data scales but produces unstable plans when data grows because (a) FPTP's "trial run" works on the live collection at trial time, and (b) plan cache entries are not automatically invalidated by data growth — they're only re-validated when a query's actual rows-examined diverges meaningfully from the cached estimate.

Oracle's CBO uses real statistics (gathered automatically or via `DBMS_STATS`) and cost-models all candidate plans before execution. Plan re-evaluation happens via SQL Plan Management or manually via stats refresh. At 10×, 100×, 1000× scale, the CBO's plan adapts; FPTP may stick.

**Expected:** At constant data, both engines pick reasonable plans and produce comparable latency. At 10×/100× growth without stats refresh, MongoDB plan-drift produces significant latency variance; Oracle remains stable. With stats refresh on both sides, both adapt.

## Article claim mapping

- Research dimension: planner stability under cardinality drift.

## Data dependencies

- Scale factor: SF0.1 → SF1 (10× growth). SF10 is out of scope for v1.0 (exceeds Oracle Free's 12 GB user-data cap).
- A 10× cardinality drift (100 K → 1 M orders) is sufficient to exercise plan-cache invalidation logic on both engines. The article's claim is about *whether the engines re-plan when cardinality drifts* — 10× is a substantial drift.
- The same data generator with the same seed at both scales ensures the *distribution* is preserved while the *cardinality* grows.

## Indexes

Inherits from S02 (a representative multi-stage pipeline).

## Workload structure

The S02 pipeline (top-100 customers by 90-day revenue with profile join) is run at three data scales sequentially. Plan caches are *not* cleared between scales — the goal is to expose what happens when the engine has cached a plan from a smaller scale and now sees larger data.

Sub-variants:

### V13-a: Stats-stale baseline
1. Load SF0.1; warm up; run S02; record plan and timings.
2. Add SF1-additional data (without re-loading SF0.1) so collection is now at SF1 scale.
3. Run S02 again (no plan-cache clear, no stats refresh). Record plan and timings.

(SF10 step omitted — exceeds Free's 12 GB user-data cap. A future EE-licensed run would extend the sequence.)

### V13-b: Stats-fresh control
Same sequence, but after each data load: `DBMS_STATS.GATHER_TABLE_STATS` on Oracle; `db.collection.getPlanCache().clear()` on Mongo.

The comparison V13-a vs V13-b shows what stale stats cost.

### V13-c: Plan stability under skew injection

Inject a localized skew: 1% of orders moved to a single customer ID, simulating a real production phenomenon (one customer dominates). Re-run S02 without stats refresh. Predicted: Mongo's plan continues to use a hash-aggregate path that's fine for the original distribution; Oracle's adaptive optimizer notices the skew and switches to a histogram-aware plan if histograms are present.

## Workload — both engines

S02 query, unchanged.

## Verification of equivalence

S02 verification process applies; re-validate at each scale.

## Predictions

| Variant | Mongo at scale | Oracle at scale | Comment |
|---------|----------------|------------------|---------|
| V13-a SF0.1 | 30 ms | 22 ms | Both fast; plan optimal |
| V13-a SF1 (stale) | 380 ms (worse plan) | 195 ms (CBO replan after stat sample) | Mongo stuck on old plan |
| V13-b SF1 (fresh) | 195 ms | 200 ms | Equivalent after stats refresh |
| V13-c skew (stale, no histogram) | 290 ms | 310 ms | Skew hurts both |
| V13-c skew (with histogram, Oracle only) | 290 ms | 90 ms | Oracle exploits the histogram |

| Prediction | Confidence |
|------------|------------|
| Mongo stale-plan latency at SF1 ≥ 2× fresh-plan latency | High |
| Oracle automatic CBO sample at runtime (`OPTIMIZER_DYNAMIC_SAMPLING`) closes most of the stale-stats gap | Medium-high |
| Mongo plan cache "isActive=false" only triggers replanning after `works × 10` divergence — slow to react | High |
| Oracle column histogram on `tier` or `region_id` produces dramatic improvement on V13-c | Medium-high |

## Pass/fail criteria

- **Pass:** V13-a Mongo SF1 ≥ 1.5× V13-b Mongo SF1 (stale stats penalty visible).
- **Pass:** V13-a Oracle SF1 within 30% of V13-b Oracle SF1 (Oracle's automatic stats refresh / dynamic sampling adapts).
- **Pass:** V13-c with-histogram Oracle ≥ 2× faster than V13-c stale Oracle.

## Failure modes

- **Mongo plan cache may still be "fresh enough"** if FPTP's threshold isn't crossed. The scenario's prediction rests on FPTP being the deciding factor; if Mongo still picks a good plan, the prediction is partially wrong but the result is still publishable as "FPTP is more resilient than the article suggests."
- **10× drift may be insufficient.** If both engines adapt within the 100 K → 1 M growth, document and propose a future Enterprise Edition run with SF10 in scope.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `scale_progression` | 0.1 → 1 | Tests plan-drift over 10× cardinality growth (within Free's cap) |
| `stats_refresh` | yes/no | A/B comparison |
| `skew_injection` | none, 1%, 5%, 25% | Sweeps skew severity |
| `histogram_present` (Oracle) | yes/no | Quantifies histogram value |
| Mongo `internalQueryFrameworkControl` | trySbeEngine, forceClassicEngine | Tests whether SBE plans are more stable than classic plans |
