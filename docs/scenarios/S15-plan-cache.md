# S15 — Plan-cache pollution under bursty workload

## Hypothesis

Production database workloads are not single-shape. A real application might emit thousands of distinct query shapes — different `$match` predicates, different `$group` keys, different `$lookup` foreign collections — within a short window. How each engine's plan cache behaves under this churn is a real architectural difference:

- MongoDB plan cache: per-collection, FPTP-based, capped at 200 entries by default. Cache eviction LRU. Each entry has an `isActive` flag that goes false when actual rowcount diverges from cached estimate (triggering replan).
- Oracle shared cursor cache: SGA-resident, far larger (GB-scale by default), with bind-aware peeking that allows different bind values to use different cached plans.

**Expected:** Under a workload of 10 K distinct query shapes in a 60-second burst, MongoDB's plan cache hit rate falls below 50% and tail latency spikes from re-planning costs. Oracle's hit rate stays above 80% and latency is more stable.

## Article claim mapping

- Research dimension: plan-cache pollution under bursty workload.

## Data dependencies

- Scale factor: SF1.

## Indexes

Inherits from base scenarios.

## Workload structure

The harness emits 10 K distinct query shapes by parameterizing a base S02-like pipeline:

```python
shapes = []
for cust_range_start in random.sample(range(0, 1_000_000), 100):
    for date_window_days in [30, 60, 90, 180]:
        for sort_field in ["revenue", "order_count", "customer_id"]:
            for limit in [10, 50, 100, 500]:
                shapes.append(make_pipeline(cust_range_start, date_window_days, sort_field, limit))
```

That's 100 × 4 × 3 × 4 = 4800 distinct shapes. To reach 10 K we add another dimension (`region_filter` ∈ regions) for ~50 K possible shapes; we sample 10 K.

The harness submits these shapes in random order, in a tight loop, for 60 seconds (after a 10s warmup that primes the plan cache with a representative subset). Each iteration's wall-clock latency is recorded.

## Workload — Oracle

The same parameterization produces SQL with bind variables:

```sql
WITH revenue AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         (SELECT SUM(li.extended_price) FROM JSON_TABLE(o.payload, '$.line_items[*]'
           COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) BETWEEN :p_cust_start AND :p_cust_end
    AND JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= :p_date_from
)
SELECT * FROM revenue
ORDER BY :p_sort_field DESC
FETCH FIRST :p_limit ROWS ONLY;
```

Oracle's bind-aware cursor sharing reuses one cursor across many bind values *if* the optimizer judges the plan equally good for the bind variants. Under bursty workloads with vastly different selectivities, Oracle creates per-selectivity plans (bind-aware peeking) and reuses them.

For Mongo, the same pipeline shape (same JSON keys) will hit the same plan cache key — even with different match values. Different sort field names *do* produce different plans because the field name is part of the cache key.

## Verification of equivalence

Per-iteration verification is impractical (10 K different queries). Instead, the harness verifies a sampled subset (1% of iterations) for full equivalence. The remaining 99% rely on the assumption that if the parameterized template is equivalence-correct, all parameterizations are.

## Predictions

| Metric | Mongo | Oracle |
|--------|-------|--------|
| Plan cache hit rate | 35% | 85% |
| p50 latency | 600 ms | 420 ms |
| p95 latency | 2.1 s | 720 ms |
| p99 latency | 5.5 s | 1.4 s |
| Throughput (qps) | 18 | 32 |
| Plan-recompile count | high (FPTP triggered hundreds of times) | low (bind-aware peeking handles selectivity drift) |

| Prediction | Confidence |
|------------|------------|
| Mongo plan cache hits < 50% under 10 K-shape workload | High |
| Oracle cursor cache hits > 75% | High |
| Mongo p99 / p50 ratio ≥ 7 | Medium-high |
| Oracle p99 / p50 ratio ≤ 4 | Medium-high |

## Pass/fail criteria

- **Pass:** Mongo plan cache hit rate < 60% AND Oracle cursor cache hit rate > 70%.
- **Strong pass:** Mongo p99/p50 ratio ≥ 5; Oracle p99/p50 ratio ≤ 4.
- **Fail (test invalid):** Both engines have low hit rates (< 30%) — the workload is more diverse than either cache can handle, and the comparison is between two equally-bad situations.

## Failure modes

- **Plan cache size limit (Mongo).** 200 entries default. We don't raise this — it's the production reality. If the user wants to test "what if we raise the limit to 5000," that's an ablation variant.
- **SGA pressure (Oracle).** With 1.2 GB SGA on Free, the shared pool is much smaller. 10 K cursors at ~30 KB each ≈ 300 MB — non-trivial fraction of SGA. The scenario may begin to *evict* cursors at the high end of the n_shapes sweep. This is itself a finding worth recording: at constrained SGA, even Oracle's cursor cache hits its limits — but the eviction policy is documented and the resulting hit-rate degradation is much gentler than Mongo's hard 200-entry cap.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `n_shapes` | 100, 1 K, 10 K, 50 K | Primary; tests how each cache scales |
| `mongo_plan_cache_size` (via `internalQueryCacheMaxEntriesPerCollection`) | 200 (default), 1000, 5000 | Ablation; quantifies cache-size impact on Mongo |
| `oracle_cursor_sharing` | EXACT, FORCE | Tests how forced cursor sharing performs at scale |
| `bind_value_skew` | uniform, lognormal | Tests bind-aware peeking under skewed selectivities |
| Concurrent workers (overlap with S12) | 1, 4, 16 | Tests cache contention under parallelism |
