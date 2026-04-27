# S08 — Window functions after a non-pushable stage

## Hypothesis

`$setWindowFields` is SBE-eligible *only if* every preceding stage is also SBE-eligible. The moment a non-pushable stage (`$facet`, `$bucketAuto`, etc.) appears earlier in the pipeline, the window function falls back to classic execution. Because window functions over large input sets are CPU- and memory-intensive, the boundary tax shows up dramatically. Oracle's window functions (`OVER`/`PARTITION BY`) are first-class operators in the iterator tree and are not affected by upstream operator choice.

**Expected ratio: 8× – 25×** in favor of Oracle when the window function is downstream of a non-pushable stage. The same window function with an SBE-clean prefix should produce a much smaller gap (~1.5–2×).

## Article claim mapping

- Claim 7: `$setWindowFields` SBE-eligibility depends on prefix.
- Claim 2 (secondary): Boundary tax compounds with operator complexity.

## Data dependencies

- Scale factor: SF1.

## Indexes

- `IX_ORD_DATE` — both sides.
- `IX_ORD_CUST_DATE` — both sides.

## Workload structure

The query: rolling 30-day-window average revenue per customer, partitioned by region. The pipeline structure is held constant; the **only** variable is whether the prefix contains a non-pushable stage.

### Variant A: Clean SBE prefix (control)

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-01-01") } } },          // SBE
  { $group: { _id: { customer_id: "$customer_id", date: "$order_date" },
              revenue: { $sum: "$line_items.extended_price" } } },        // SBE
  { $lookup: { from: "customers", localField: "_id.customer_id",
               foreignField: "customer_id", as: "c" } },                  // SBE
  { $unwind: "$c" },
  { $setWindowFields: {
      partitionBy: "$c.region_id",
      sortBy: { "_id.date": 1 },
      output: {
        rolling30dAvg: {
          $avg: "$revenue",
          window: { range: [-30, 0], unit: "day" }
        }
      }
  }}
])
```

### Variant B: Non-pushable stage in prefix (`$facet` at position 2)

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-01-01") } } },
  { $facet: {                                                              // ❌ classic
      revenue: [
        { $group: { _id: { customer_id: "$customer_id", date: "$order_date" },
                    revenue: { $sum: "$line_items.extended_price" } } }
      ]
  }},
  { $unwind: "$revenue" },
  { $replaceRoot: { newRoot: "$revenue" } },
  { $lookup: { from: "customers", localField: "_id.customer_id",
               foreignField: "customer_id", as: "c" } },
  { $unwind: "$c" },
  { $setWindowFields: {                                                    // ⚠️ classic (post-boundary)
      partitionBy: "$c.region_id",
      sortBy: { "_id.date": 1 },
      output: { rolling30dAvg: { $avg: "$revenue", window: { range: [-30, 0], unit: "day" } } }
  }}
])
```

The intentional `$facet`+`$unwind`+`$replaceRoot` insertion is awkward but represents a real pattern: ad-hoc multi-faceted aggregations followed by window-function rollups.

## Workload — Oracle

The Oracle CTE is *the same for both variants* — the CBO doesn't have an analogous boundary:

```sql
WITH daily_customer_revenue AS (
  SELECT
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    JSON_VALUE(o.payload, '$.order_date'  RETURNING DATE)   AS order_date,
    (SELECT SUM(li.extended_price)
     FROM JSON_TABLE(o.payload, '$.line_items[*]'
       COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= DATE '2024-01-01'
),
joined AS (
  SELECT d.customer_id, d.order_date, d.revenue, c.region_id
  FROM daily_customer_revenue d
  JOIN customers c USING (customer_id)
)
SELECT
  customer_id,
  order_date,
  revenue,
  region_id,
  AVG(revenue) OVER (
    PARTITION BY region_id
    ORDER BY order_date
    RANGE BETWEEN INTERVAL '30' DAY PRECEDING AND CURRENT ROW
  ) AS rolling_30d_avg
FROM joined;
```

Expected plan: WINDOW SORT operator with the partition+sort key, fed by a hash join of `daily_customer_revenue` (built per-row, not materialized) and `customers`. No materialization barrier.

## Verification of equivalence

Sort by `(region_id, customer_id, order_date)`, hash. `rolling_30d_avg` is a floating average — relative tolerance `1e-9`.

Note: window-function semantics are subtly different across engines. MongoDB's `range: [-30, 0], unit: day` is "30 days back to current row" — Oracle's `RANGE BETWEEN INTERVAL '30' DAY PRECEDING AND CURRENT ROW` matches semantically. Verify on a small subset before the timing run.

## Predictions

| Variant | Predicted Mongo median | Predicted Oracle median | Ratio |
|---------|------------------------|-------------------------|-------|
| A (clean SBE prefix) | 1.6 s | 850 ms | 1.9× |
| B (`$facet` at pos 2) | 5.4 s | 850 ms | 6.4× |
| B-`$bucketAuto` | 4.8 s | 850 ms | 5.6× |
| B-`$graphLookup` | 7.5 s | 850 ms | 8.8× |

| Prediction | Confidence |
|------------|------------|
| Variant A `$setWindowFields` runs in SBE | High |
| Variant B `$setWindowFields` falls back to classic; explain shows `$cursor` boundary at `$facet` | Very high |
| Mongo Variant B / Variant A ratio ≥ 3× | High |
| Oracle invariant across A/B/C/D within ±10% | Very high |
| Window-function spill metrics visible at large partitions (≥ 100 K rows per region) | Medium |

## Pass/fail criteria

- **Strong pass:** Mongo Variant B ≥ 3× Variant A; Oracle flat across variants; Mongo classic-engine confirmed for Variant B's window function.
- **Pass:** Mongo Variant B ≥ 2× Variant A.
- **Fail:** Variants converge — investigate; either the boundary detection is wrong or `$setWindowFields` actually still pushes despite the `$facet` upstream.

## Failure modes

- Window function spill at high `partitionBy` cardinality. Recorded; predicted but not the dominant effect at SF1.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| Variant | A, B-`$facet`, B-`$bucketAuto`, B-`$graphLookup` | Tests boundary-tax invariance to triggering stage |
| `window_size` | 7d, 30d, 90d, 365d | Bigger windows = more rows per output → more SBE-prefix benefit when applicable |
| `partition_cardinality` | 5, 50, 500 (regions; via altered region table) | Tests window-spill behavior |
| Cold cache | yes/no | Exposes IO cost of the partitioned sort |
