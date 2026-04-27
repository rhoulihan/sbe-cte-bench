# S02 — SBE-prefix best case

## Hypothesis

A multi-stage pipeline composed *entirely* of SBE-eligible stages — `$match`, `$group`, `$sort`, `$limit`, `$lookup` against an unsharded collection, `$project` — is the best case for MongoDB. The scenario establishes Mongo's performance ceiling. **Expected ratio: 1.2× – 2.0×** (Oracle still wins on planning freedom, but the gap is small).

## Article claim mapping

- Claim 1: "SBE accelerates a prefix of the pipeline." This scenario is the prefix that *never ends* — every stage is SBE-eligible — so the prefix is the whole pipeline.
- Claim 11: "CBO produces a single iterator tree." Oracle's CTE is one inlined block; the explain plan should show no materialized CTEs.

## Data dependencies

- Scale factor: SF1.
- No extension flags.

## Indexes

Standard index audit table (`04-indexes.md`):

- `IX_ORD_CUST_DATE` — both sides.
- `IX_ORD_DATE` — both sides.
- `IX_CUST_PK` — both sides.
- `IX_CAT_PK` — both sides.

Plus, scenario-specific:

- MongoDB: `db.orders.createIndex({ order_date: -1, customer_id: 1 })` to support the top-N + revenue rollup pattern.
- Oracle: composite function-based index on `(JSON_VALUE(payload, '$.order_date' DESC), JSON_VALUE(payload, '$.customer_id'))`.

## Workload — MongoDB

```javascript
db.orders.aggregate([
  // SBE ✅ — IXSCAN on IX_ORD_DATE
  { $match: { order_date: { $gte: ISODate("2024-08-01") } } },

  // SBE ✅ — group with $sum
  { $group: {
      _id: "$customer_id",
      revenue_90d: { $sum: { $sum: "$line_items.extended_price" } },
      order_count: { $sum: 1 }
  }},

  // SBE ✅ — sort with limit (top-N optimization)
  { $sort: { revenue_90d: -1 } },
  { $limit: 100 },

  // SBE ✅ — single-collection $lookup against unsharded foreign
  { $lookup: {
      from: "customers",
      localField: "_id",
      foreignField: "customer_id",
      as: "customer"
  }},
  { $unwind: "$customer" },

  // SBE ✅ — projection
  { $project: {
      customer_id: "$_id",
      customer_name: "$customer.name",
      tier: "$customer.tier",
      region_id: "$customer.region_id",
      revenue_90d: 1,
      order_count: 1,
      _id: 0
  }}
])
```

Seven stages, all SBE-eligible. Top-100 customers by 90-day revenue with profile data joined.

Expected explain:

- All stages SBE-pushed.
- `winningPlan.stage` shows `IXSCAN -> GROUP -> SORT -> LIMIT -> EQ_LOOKUP_UNWIND -> PROJECTION_DEFAULT`.
- `nReturned: 100`.
- No `$cursor` wrapper anywhere in the explain output.

## Workload — Oracle

```sql
WITH recent_orders AS (
  SELECT
    JSON_VALUE(payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    (SELECT SUM(li.extended_price)
     FROM JSON_TABLE(
       payload, '$.line_items[*]'
       COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS order_amount
  FROM orders_doc
  WHERE JSON_VALUE(payload, '$.order_date' RETURNING DATE) >= ADD_MONTHS(SYSDATE, -3)
),
top_clients AS (
  SELECT
    customer_id,
    SUM(order_amount) AS revenue_90d,
    COUNT(*)          AS order_count
  FROM recent_orders
  GROUP BY customer_id
  ORDER BY revenue_90d DESC
  FETCH FIRST 100 ROWS ONLY
)
SELECT
  t.customer_id,
  c.name AS customer_name,
  c.tier,
  c.region_id,
  t.revenue_90d,
  t.order_count
FROM top_clients t
JOIN customers c USING (customer_id);
```

Two single-reference CTEs, both inlined. One join. Top-N driven by `FETCH FIRST 100`.

Expected plan:

- No `TEMP TABLE TRANSFORMATION` step (CTEs inlined).
- Index range scan on the date-based index.
- Hash group-by with workarea optimal mode (well under 100 MB).
- Sort with `STOPKEY` (top-N optimization).
- Hash join with `customers` (small build side).
- Total cost under a few thousand cost units; `cardinality` reasonably accurate.

## Verification of equivalence

Sort by `customer_id`, hash. The customer profile columns (`name`, `tier`, `region_id`) are deterministic from the seed; `revenue_90d` compares with relative tolerance `1e-9`; `order_count` exact.

## Predictions

| Prediction | Confidence | Rationale |
|------------|------------|-----------|
| Median ratio Mongo/Oracle ∈ [1.1, 2.0] | High | Mongo's best case; Oracle's CBO still has planning advantages (sort+limit composes natively, hash-join cardinality estimate informs build side, multivalue index can prune line items). |
| Mongo explain shows zero `$cursor` wrappers | High | All stages SBE-eligible. |
| Oracle plan shows zero materialized CTEs | High | Single-reference CTEs inline by default. |
| Oracle workarea modes all "optimal" | Medium-high | 100 distinct customers fits trivially. |
| Both `cv < 0.05` warm-cache | High | Deterministic, indexed plan. |

## Pass/fail criteria

- **Pass:** Ratio in [1.1, 2.0] AND no SBE→classic boundary on Mongo AND single inlined plan on Oracle.
- **Conditional pass:** Ratio in [0.9, 2.5] but explain plans confirm SBE on Mongo; the gap is just engine-implementation overhead, not architectural. Note in writeup.
- **Fail:** Ratio > 2.5×, indicating something other than the SBE-prefix architecture is at play; investigate.

## Failure modes

None expected. If Mongo errors here, the configuration is wrong — likely `internalQueryFrameworkControl` set to classic.

## Variations / sweep parameters

- **Ablation:** re-run with `internalQueryForceClassicEngine: true`. The delta vs the SBE run quantifies what SBE actually buys on the eligible prefix. Recorded as `S02-classic` in the run record.
- **Result-set size:** vary `$limit` ∈ {10, 100, 1000, 10000}. Larger limits expose whether top-N optimization generalizes.
- **Date window:** vary `order_date >= …` ∈ {30, 60, 90, 180, 365 days}. Selectivity sweep; tests both indexes' cost models.
