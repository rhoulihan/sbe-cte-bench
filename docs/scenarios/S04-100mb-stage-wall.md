# S04 — 100 MB stage wall

## Hypothesis

`$group` and `$sort` (and `$bucket`/`$bucketAuto`/`$setWindowFields`) have a per-stage 100 MB working-set cap. When the working set crosses that threshold, the stage spills to disk via `allowDiskUseByDefault: true`. The spill adds 5–50× to wall-clock time depending on disk speed and operator restartability. Oracle's workarea handling — sized by the CBO at plan time and bounded by `PGA_AGGREGATE_TARGET` (600 MB on Free) — produces a smoother degradation curve.

**On Oracle Free, the comparison is particularly informative**: with PGA capped at 600 MB total, Oracle's workarea grants per operator are bounded to ~50–150 MB depending on concurrent operator count. So *both* engines hit per-operator memory limits at roughly the same working-set size. The architectural question is whether they degrade *the same way* when they cross those limits — or whether MongoDB's hard 100 MB stage cap is more punitive than Oracle's elastic workarea-bounded grant.

**Expected:** A clear knee in MongoDB's latency curve at working-set ≈ 100 MB. Oracle's curve degrades more gracefully — the workarea size policy adapts based on cardinality estimates and converts gracefully from optimal to one-pass to multi-pass.

## Article claim mapping

- Claim 3: 100 MB per-stage cap forces disk spill.

## Data dependencies

- Scale factor: SF1 with `--include-extension=S04` (deep-skew extension: 2 K artificial categories with repeated cardinality, ~200 K intermediate group rows).
- The accumulator-fields knob (`$addToSet` vs `$push` over varying-length nested arrays) lets the harness scale intermediate state from ~25 MB to ~250 MB without changing the data — by changing what the `$group` stage materializes per group.

## Indexes

- `IX_ORD_DATE` — both sides.
- `IX_ORD_LI_PRODUCT` — both sides.
- `IX_PROD_CAT` — both sides.

## Workload — MongoDB

The query: revenue by category, with a per-category list of customer IDs and the running set of distinct order dates.

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2023-01-01") } } },  // SBE ✅
  { $unwind: "$line_items" },                                    // SBE ✅
  { $lookup: {                                                   // SBE ✅ (unsharded)
      from: "products",
      localField: "line_items.product_id",
      foreignField: "product_id",
      as: "product"
  }},
  { $unwind: "$product" },
  { $group: {                                                    // SBE ✅, but spill-prone
      _id: "$product.category_id",
      revenue: { $sum: "$line_items.extended_price" },
      customers: { $addToSet: "$customer_id" },
      order_dates: { $addToSet: "$order_date" }
  }},
  { $sort: { revenue: -1 } }                                     // SBE ✅, spill-prone
])
```

The accumulator fields (`customers` and `order_dates` `$addToSet`) cause working-set growth in proportion to category cardinality.

The harness sweeps a knob: `working_set_mb` ∈ {25, 50, 75, 100, 150, 200, 250}. The knob is implemented by varying the `$addToSet` accumulator field set (more fields per group → larger per-group state) and the `$match` selectivity together, so the working set crosses the 100 MB cap at a known knob value.

Expected explain at WS=150 MB:

- `$group` reports `groupSpills > 0`, `groupSpilledBytes ≈ 50 MB`, `groupSpilledRecords > 0`.
- `$sort` may also spill if its input is large.

## Workload — Oracle

```sql
WITH order_lines AS (
  SELECT
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) AS order_date,
    li.product_id,
    li.extended_price
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id NUMBER PATH '$.product_id',
           extended_price NUMBER PATH '$.extended_price'
         )) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= DATE '2023-01-01'
)
SELECT
  p.category_id,
  SUM(ol.extended_price) AS revenue,
  CAST(COLLECT(DISTINCT ol.customer_id) AS sys.odcinumberlist) AS customers,
  CAST(COLLECT(DISTINCT ol.order_date)  AS sys.odcidatelist)   AS order_dates
FROM order_lines ol
JOIN products p ON p.product_id = ol.product_id
GROUP BY p.category_id
ORDER BY revenue DESC;
```

The `COLLECT DISTINCT` aggregation produces the equivalent of `$addToSet`. `sys.odcinumberlist` and `sys.odcidatelist` are built-in collection types.

Expected plan: hash join, hash group-by, workarea bound by PGA grant. At PGA target = 600 MB (Oracle Free's cap) and the largest variant working-set ≈ 250 MB, we expect optimal-mode execution at WS ≤ 100 MB, one-pass at WS ∈ [150, 200], possibly multi-pass at WS = 250 MB.

## Verification of equivalence

The result has 10 K rows. Sort by `category_id`, hash:

- `revenue`: relative tolerance `1e-9`.
- `customers`: convert both sides to sorted arrays; hash.
- `order_dates`: convert both sides to sorted arrays of date strings; hash.

`$addToSet` and `COLLECT DISTINCT` produce semantically equivalent sets but may emit them in different orders. Sorting before hashing fixes that.

## Predictions

| working_set_mb | Predicted Mongo median | Predicted Oracle median | Notes |
|----------------|------------------------|-------------------------|-------|
| 25  | 750 ms | 600 ms | Both in-memory; ratio ~1.25× |
| 50  | 1.2 s | 850 ms | Both in-memory; ratio ~1.4× |
| 75  | 1.7 s | 1.05 s | Both in-memory; Mongo close to cap |
| 100 | **3.5 s (spill begins)** | 1.3 s | Mongo at the threshold |
| 150 | 6.5 s (spill) | 1.7 s | Mongo definitively spilled |
| 200 | 11 s | 2.3 s (one-pass) | Oracle may begin one-pass |
| 250 | 18+ s | 3.2 s (one or multi-pass) | Oracle's smaller PGA shows; still graceful |

| Prediction | Confidence |
|------------|------------|
| Mongo `$group` shows `groupSpills > 0` at WS ≥ 100 MB | High |
| Oracle workarea mode = "optimal" at WS ≤ 100 MB | High |
| Oracle workarea mode = "one-pass" at WS ∈ [150, 250] MB | Medium-high (PGA target = 600 MB constrains the optimal threshold) |
| Mongo latency knee at WS = 100 MB, ratio jump ≥ 2× | High |
| Oracle latency curve smoother than Mongo's; ratio across WS sweep ≤ 6× | High |

## Pass/fail criteria

- **Strong pass:** Knee visible in Mongo curve at predicted WS; Oracle smooth. Both monotonic.
- **Pass:** Knee location ±50 MB of prediction; Oracle within 10× of best across the sweep.
- **Fail:** Mongo doesn't spill (suggests harness misconfiguration; check `allowDiskUseByDefault` and per-stage limits) — re-run with explicit `allowDiskUse: true` on the aggregate command.

## Failure modes

- **Disk full.** Mongo's spill can write substantial temp data. The bench host's `/tmp` (or wherever WiredTiger spill goes) needs ≥ 50 GB free.
- **Iteration time-out.** At WS=500 MB on a slow disk, an iteration could exceed 10 minutes. Tunable per-iteration timeout (default 5 min) should be lifted to 30 min for this scenario.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `working_set_mb` | 25, 50, 75, 100, 150, 200, 250 | Primary sweep |
| `internalDocumentSourceGroupMaxMemoryBytes` | default (100 MB), 500 MB | Ablation: raises Mongo's per-stage cap. Quantifies what % of the slowdown is the cap vs the architecture. |
| `PGA_AGGREGATE_TARGET` | default (600 MB on Free), 1 GB (only if Free permits — verify) | Ablation on Oracle: tests whether Oracle's smaller-PGA disadvantage produces equivalent spill cost to Mongo's hard cap. |
| Cold-cache | yes/no | Cold-cache adds the read-time of the orders collection; the spill cost is on top of that. |
