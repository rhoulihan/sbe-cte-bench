# S10 — Top-N optimization with downstream stages

## Hypothesis

Both engines recognize sort+limit (top-N) as an optimization — MongoDB's SBE supports `$sort` + `$limit` fusion; Oracle's CBO supports `FETCH FIRST N ROWS ONLY` with `STOPKEY` row-source. The interesting question is what happens *after* the top-N, when subsequent stages enrich, transform, or aggregate further.

In Mongo, downstream stages process the surviving N rows correctly — the top-N optimization holds. But if any downstream stage triggers SBE→classic fallback, the top-N is no longer "fused into the sort"; it's evaluated, then the survivors are re-materialized for classic processing. The cost shows up in subtle ways.

In Oracle, the entire query is one tree. STOPKEY informs all upstream costing decisions and the join-order optimizer chooses to eagerly limit the driving side.

**Expected:** Top-N alone is comparable on both. Top-N + downstream `$lookup` is comparable. Top-N + downstream non-pushable stage shows a gap.

## Article claim mapping

- Claim 9: Top-N optimization composes differently with downstream stages.

## Data dependencies

- Scale factor: SF1.

## Indexes

- `IX_ORD_CUST_DATE` — both sides.
- `IX_CUST_REGION` — both sides.
- `IX_PROD_PK`, `IX_CAT_PK` — both sides.

## Workload structure

Three variants, all "top 100 customers by 90-day revenue":

### Variant A — Top-N alone

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-08-01") } } },
  { $group: { _id: "$customer_id",
              revenue: { $sum: "$line_items.extended_price" } }},
  { $sort: { revenue: -1 } },
  { $limit: 100 }
])
```

### Variant B — Top-N + downstream `$lookup` (SBE-eligible)

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-08-01") } } },
  { $group: { _id: "$customer_id",
              revenue: { $sum: "$line_items.extended_price" } }},
  { $sort: { revenue: -1 } },
  { $limit: 100 },
  { $lookup: { from: "customers", localField: "_id",
               foreignField: "customer_id", as: "c" }},
  { $unwind: "$c" },
  { $project: { customer_id: "$_id", revenue: 1, name: "$c.name", region_id: "$c.region_id" } }
])
```

### Variant C — Top-N + downstream `$facet` (boundary tax)

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-08-01") } } },
  { $group: { _id: "$customer_id",
              revenue: { $sum: "$line_items.extended_price" } }},
  { $sort: { revenue: -1 } },
  { $limit: 100 },
  { $facet: {                                              // ❌ classic
      summary: [
        { $group: { _id: null, total: { $sum: "$revenue" }, avg: { $avg: "$revenue" }}}
      ],
      detail: [
        { $lookup: { from: "customers", localField: "_id",
                     foreignField: "customer_id", as: "c" }},
        { $unwind: "$c" }
      ]
  }}
])
```

The `$facet` operates on only 100 input rows. The boundary tax is small *in absolute terms* (100 docs × per-row materialization), but Mongo's `$facet` setup, branch isolation, and intermediate result merging carry fixed overhead that Oracle's equivalent does not.

## Workload — Oracle (one query each variant; using set algebra for variant C)

### Variant A
```sql
WITH revenue_by_customer AS (
  SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
         (SELECT SUM(li.extended_price)
          FROM JSON_TABLE(o.payload, '$.line_items[*]'
            COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= ADD_MONTHS(SYSDATE, -3)
)
SELECT customer_id, SUM(revenue) AS total_revenue
FROM revenue_by_customer
GROUP BY customer_id
ORDER BY total_revenue DESC
FETCH FIRST 100 ROWS ONLY;
```

### Variant B
```sql
-- Same revenue_by_customer CTE
WITH revenue_by_customer AS (...),
top_100 AS (
  SELECT customer_id, SUM(revenue) AS total_revenue
  FROM revenue_by_customer
  GROUP BY customer_id
  ORDER BY total_revenue DESC
  FETCH FIRST 100 ROWS ONLY
)
SELECT t.customer_id, t.total_revenue, c.name, c.region_id
FROM top_100 t
JOIN customers c USING (customer_id);
```

### Variant C
```sql
WITH revenue_by_customer AS (...),
top_100 AS (...)
SELECT
  ( SELECT JSON_OBJECT('total' VALUE SUM(total_revenue),
                       'avg'   VALUE AVG(total_revenue))
    FROM top_100 )                                        AS summary,
  CURSOR( SELECT t.customer_id, t.total_revenue, c.name, c.region_id
          FROM top_100 t JOIN customers c USING (customer_id) ) AS detail
FROM dual;
```

The Oracle Variant C uses a `CURSOR` expression to mimic the multi-branch shape of `$facet`. There are simpler ways (issue two SELECTs from the same CTE) but the CURSOR variant most closely matches the Mongo `$facet` semantics. Either form is acceptable; pick the one that produces the equivalent result shape.

## Verification of equivalence

A: sort by `total_revenue desc`, hash. B: sort by `customer_id`, hash. C: extract summary and detail, hash separately, compare.

## Predictions

| Variant | Predicted Mongo median | Predicted Oracle median | Ratio |
|---------|------------------------|-------------------------|-------|
| A | 580 ms | 285 ms | 2.0× |
| B | 685 ms | 305 ms | 2.2× |
| C (facet) | 1.6 s | 410 ms | 3.9× |

| Prediction | Confidence |
|------------|------------|
| Both engines: top-100 + downstream `$lookup` (Variant B) costs ≤ 1.5× of variant A | High |
| Mongo Variant C ≥ 2× Variant A | High |
| Oracle Variant C ≥ 1.4× Variant A but ≤ 1.8× | Medium |
| Mongo SBE explain shows top-N "SORT_KEY" or equivalent fused operator on Variant A | High |
| Oracle plan shows STOPKEY on the inner CTE in Variant A | Very high |

## Pass/fail criteria

- **Pass:** Variant A ratio ≤ 2.5× AND Variant C ratio > Variant A ratio (the gap widens with `$facet`).
- **Fail (test invalid):** Variant C is *faster* than Variant A on Mongo — would indicate something fishy with the workload or harness.

## Failure modes

None expected.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| Variant | A, B, C | Primary |
| `limit` | 10, 100, 1000, 10000 | Tests how top-N optimization degrades as N grows |
| Cold cache | yes/no | |
