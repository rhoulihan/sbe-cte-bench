# S03 — Boundary tax

## Hypothesis

A pipeline whose first SBE-incompatible stage appears at position `k` will pay per-document materialization cost on every stage from `k` to the end. As `k` increases, more of the pipeline runs in classic engine; as `k` decreases, less of it does. Plotting median latency vs `k` should show the slope of the boundary tax. **Expected: latency rises non-trivially as `k` shifts later** (i.e., a longer classic-engine suffix), and the per-stage cost should be approximately linear in the number of post-boundary stages — exposing the per-row materialization tax.

The Oracle equivalent has no boundary; latency should be approximately flat across all `k` variants.

## Article claim mapping

- Claim 1: SBE covers a prefix.
- Claim 2: SBE→classic boundary materializes BSON per row per remaining stage.
- Claim 6: `$facet`, `$bucketAuto`, etc. are classic-only.

## Data dependencies

- Scale factor: SF1.
- No extension flags.

## Indexes

- `IX_ORD_DATE` — both sides.
- `IX_ORD_CUST_DATE` — both sides.
- `IX_CUST_REGION` — both sides.
- `IX_PROD_CAT` — both sides.

## Workload structure

The pipeline produces "top-50 customers by 90-day revenue, grouped/bucketed by region, with rolling 30-day average." The same 8 stages appear in every variant. The variant changes *which* stage is the first non-pushable one — by inserting `$bucketAuto` (always classic) at position `k ∈ {2, 3, 4, 5, 6, 7, 8}`.

The fixed pipeline (positions 1–8):

```
1. $match { order_date >= 90d ago, status != 'cancelled' }
2. $unwind $line_items
3. $group { _id: { customer_id, region_id }, revenue: $sum }
4. $sort { revenue: -1 }
5. $limit 5000
6. $lookup customers
7. $project { customer_id, region, revenue, ... }
8. $setWindowFields { rollingAvg }
```

The variant:

```
{ $bucketAuto: { groupBy: "$region_id", buckets: 10 } }
```

inserted at position `k ∈ {2..8}`. Remember `$bucketAuto` is classic-only, so the SBE prefix length is `k - 1`. We sweep `k = 2, 4, 6, 8` for the primary chart and `k = 3, 5, 7` for verification.

A reference run at `k = 0` (no `$bucketAuto`) measures the all-SBE baseline.

## Workload — MongoDB (k = 4 example)

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-08-01") }, status: { $ne: "cancelled" } } },
  { $unwind: "$line_items" },
  { $group: {
      _id: { customer_id: "$customer_id" },
      revenue: { $sum: "$line_items.extended_price" }
  }},
  // ↓ Boundary at k=4: bucketAuto is classic-only
  { $bucketAuto: { groupBy: "$revenue", buckets: 10 } },
  { $sort: { _id: 1 } },
  { $limit: 5000 },
  { $lookup: { from: "customers", localField: "_id", foreignField: "customer_id", as: "c" } },
  { $project: { revenue: 1, c: 1 } }
])
```

For each variant `k`, the harness:

1. Constructs the pipeline with `$bucketAuto` at position `k`.
2. Runs explain — confirms `$cursor` wrapper appears at stage `k`.
3. Records `sbe_prefix_length: k - 1` in the run record.
4. Runs 20 timed iterations.

## Workload — Oracle (k-invariant)

```sql
WITH unwound AS (
  SELECT
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    li.extended_price
  FROM orders_doc o,
       JSON_TABLE(
         o.payload, '$.line_items[*]'
         COLUMNS (extended_price NUMBER PATH '$.extended_price')) li
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= ADD_MONTHS(SYSDATE, -3)
    AND JSON_VALUE(o.payload, '$.status') <> 'cancelled'
),
revenue_by_customer AS (
  SELECT
    customer_id,
    SUM(extended_price) AS revenue
  FROM unwound
  GROUP BY customer_id
),
bucketed AS (
  SELECT
    customer_id,
    revenue,
    NTILE(10) OVER (ORDER BY revenue) AS bucket
  FROM revenue_by_customer
)
SELECT
  b.customer_id,
  b.revenue,
  b.bucket,
  c.name,
  c.region_id
FROM bucketed b
JOIN customers c USING (customer_id)
ORDER BY b.bucket, b.revenue DESC
FETCH FIRST 5000 ROWS ONLY;
```

The Oracle CTE structure does not change with `k`. The CBO sees the whole inlined query block regardless of how the equivalent MongoDB pipeline orders its stages. **This is the architectural point.**

## Verification of equivalence

Sort by `(bucket, customer_id)`, hash. `revenue` compared with relative tolerance `1e-9`; `bucket` exact.

Note: `$bucketAuto` and `NTILE(10) OVER (ORDER BY ...)` produce *deterministic* but *not necessarily identical* bucket boundaries on different engines because of tie-handling at boundaries. To make the result hashable: the result projection rounds `revenue` to 2 decimal places and the bucket boundary is computed by the harness from the unrounded distribution after both engines emit their results. Effectively we verify the *bucket counts* match (10 buckets, ~500 customers each) rather than identical bucket assignments. Documented limitation.

## Predictions

For Mongo:

| Variant | Predicted median (relative to k=0 baseline) | Confidence |
|---------|---------------------------------------------|------------|
| `k=0` (no `$bucketAuto`) | 1.0× | High |
| `k=8` (`$bucketAuto` at end, single classic stage) | 1.3×–1.6× | High |
| `k=6` (`$bucketAuto` at 6, 3 classic stages) | 2.0×–2.8× | Medium-high |
| `k=4` (`$bucketAuto` at 4, 5 classic stages) | 3.0×–4.5× | Medium-high |
| `k=2` (`$bucketAuto` early, 7 classic stages) | 4.0×–6.0× | Medium |
| Slope of `latency` vs `position-of-bucketAuto` (regression) | Negative slope, ≥ 0.4× per position, R² ≥ 0.7 | Medium |

For Oracle:

| Variant | Predicted median (relative to itself across k) | Confidence |
|---------|------------------------------------------------|------------|
| All variants | within ±10% of each other | High |

## Pass/fail criteria

- **Strong pass:** Mongo latency rises monotonically as `k` decreases (more classic-stage suffix). Slope coefficient ≥ 0.3× per position. Oracle latency is flat (max-min < 15% of median).
- **Pass:** Mongo `k=2` median ≥ 2× Mongo `k=8` median. Oracle flat within ±15%.
- **Fail (claim partially confirmed):** Mongo shows latency increase but not monotonic; investigate which `k` is the outlier.
- **Fail (claim refuted):** Mongo latency does not rise meaningfully with classic-suffix length (< 1.5× across the sweep). Update the article.

## Failure modes

If `k=2` produces a result so slow that the harness times out (>5 minutes), record the timeout and continue with the next variant. The timeout itself is a finding — the boundary tax can be catastrophic.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `boundary_position` (k) | 0, 2, 3, 4, 5, 6, 7, 8 | Primary sweep; produces the latency-vs-position chart. |
| `non_pushable_stage` | `$bucketAuto`, `$facet`, `$graphLookup` | Confirms the boundary tax is invariant to *which* non-pushable stage triggers it. Run only for `k=4`. |
| `internalQueryForceClassicEngine` | `false`, `true` | Ablation: forcing classic everywhere should make Mongo latency uniformly bad and roughly equal to the worst variant. Tests whether "latency = classic-stage count × per-row cost" is the right model. |
