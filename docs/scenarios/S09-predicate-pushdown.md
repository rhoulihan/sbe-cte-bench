# S09 — Predicate pushdown / join reordering

## Hypothesis

The same logical query — *"orders with high-value line items from premium customers in EMEA region last quarter"* — can be expressed in MongoDB in multiple stage orderings that produce identical results but vastly different performance, because MongoDB has limited capability to push predicates across stage boundaries (especially across `$lookup` and `$unwind`). Oracle's CBO sees the inlined CTE as a single query block, applies predicate move-around, picks the optimal join order, and produces the same plan regardless of how the developer wrote the query.

**Expected ratio:** Oracle is invariant to query syntax (within 5%); MongoDB shows a 5–25× gap between "well-ordered" and "poorly-ordered" pipelines.

## Article claim mapping

- Claim 8: CBO reorders inlined CTEs; MongoDB cannot reorder across `$facet`/`$lookup`/`$bucketAuto`.

## Data dependencies

- Scale factor: SF1.
- Region selectivity: ~10 EMEA regions out of 50 total.
- Tier selectivity: ~3% platinum customers.

## Indexes

- `IX_ORD_CUST_DATE` — both sides.
- `IX_CUST_REGION` — both sides.
- `IX_PROD_CAT` — both sides.

## Workload structure

The query in three different MongoDB pipeline shapes — A, B, C — all returning identical result sets. The *expected* good ordering is A; B and C are realistic anti-patterns. Oracle has one CTE — it doesn't matter how the developer reorders the WITH clauses.

### Variant A — Well-ordered pipeline (apply predicates first)

```javascript
db.orders.aggregate([
  // Apply selective predicates first
  { $match: {
      order_date: { $gte: ISODate("2024-10-01"), $lt: ISODate("2025-01-01") }
  }},
  // Lookup customers (still small after $match)
  { $lookup: { from: "customers", localField: "customer_id",
               foreignField: "customer_id", as: "c" }},
  { $unwind: "$c" },
  // Filter on customer attributes (post-lookup but only on the survivors of $match)
  { $match: { "c.tier": "platinum", "c.region_id": { $in: emeaRegionIds } }},
  { $unwind: "$line_items" },
  { $match: { "line_items.extended_price": { $gte: 500 } }},
  { $project: {
      order_id: 1,
      customer_name: "$c.name",
      product_id: "$line_items.product_id",
      extended_price: "$line_items.extended_price"
  }}
])
```

### Variant B — Anti-pattern: lookup first, filter later

```javascript
db.orders.aggregate([
  { $lookup: { from: "customers", localField: "customer_id",
               foreignField: "customer_id", as: "c" }},
  { $unwind: "$c" },
  { $unwind: "$line_items" },
  { $match: {
      order_date: { $gte: ISODate("2024-10-01"), $lt: ISODate("2025-01-01") },
      "c.tier": "platinum",
      "c.region_id": { $in: emeaRegionIds },
      "line_items.extended_price": { $gte: 500 }
  }},
  { $project: { /* same projection */ }}
])
```

The `$match` is identical in selectivity but appears *after* the join and unwind. Mongo's optimizer should push some of the `order_date` predicate down (it does, to a degree), but it cannot push the `c.tier` or `c.region_id` predicate into the orders scan, because those fields don't exist there. The result: the lookup runs over the entire orders collection, materializes per-line-item documents, then filters.

### Variant C — Anti-pattern: facet wrapping disables pushdown

```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-10-01"), $lt: ISODate("2025-01-01") } } },
  { $facet: {
      enriched: [
        { $lookup: { from: "customers", localField: "customer_id",
                     foreignField: "customer_id", as: "c" }},
        { $unwind: "$c" },
        { $unwind: "$line_items" }
      ]
  }},
  { $unwind: "$enriched" },
  { $replaceRoot: { newRoot: "$enriched" } },
  { $match: {
      "c.tier": "platinum",
      "c.region_id": { $in: emeaRegionIds },
      "line_items.extended_price": { $gte: 500 }
  }},
  { $project: { /* same projection */ }}
])
```

Wrapping the lookup-and-unwind chain in `$facet` is a pattern users do for "build several enrichments side by side" — but it *prevents predicate pushdown across the `$facet` boundary entirely*, and forces classic execution from `$facet` onward.

## Workload — Oracle (one query for all variants)

```sql
WITH premium_emea_orders AS (
  SELECT
    JSON_VALUE(o.payload, '$.order_id'    RETURNING NUMBER) AS order_id,
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id
  FROM orders_doc o
  WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= DATE '2024-10-01'
    AND JSON_VALUE(o.payload, '$.order_date' RETURNING DATE)  < DATE '2025-01-01'
),
joined AS (
  SELECT po.order_id, po.customer_id, c.name AS customer_name
  FROM premium_emea_orders po
  JOIN customers c
    ON c.customer_id = po.customer_id
  WHERE c.tier = 'platinum' AND c.region_id IN (SELECT region_id FROM regions WHERE country IN ('DE','FR','IT','ES','UK','PL','NL','BE','SE','DK'))
)
SELECT
  j.order_id,
  j.customer_name,
  li.product_id,
  li.extended_price
FROM joined j
JOIN orders_doc o ON JSON_VALUE(o.payload, '$.order_id' RETURNING NUMBER) = j.order_id,
     JSON_TABLE(o.payload, '$.line_items[*]'
       COLUMNS (
         product_id NUMBER PATH '$.product_id',
         extended_price NUMBER PATH '$.extended_price'
       )) li
WHERE li.extended_price >= 500;
```

The CBO is free to reorder this any way it pleases. Expected plan:

1. IXSCAN on `IX_ORD_DATE_ORA` for the orders_doc → premium_emea_orders.
2. HASH JOIN with `customers` filtered on `tier = 'platinum'` (small build side).
3. HASH JOIN back with the original `orders_doc` to fetch line_items via `JSON_TABLE`.
4. Filter on `extended_price >= 500` evaluated lazily on the unnested rows.

**Critical**: a "scrambled" SQL — say, `joined` as the first CTE and `premium_emea_orders` as the second — produces an *identical* plan, because the CBO sees the whole inlined block.

## Verification of equivalence

Sort by `(order_id, product_id)`, hash.

## Predictions

| Variant | Predicted Mongo median | Predicted Oracle median | Notes |
|---------|------------------------|-------------------------|-------|
| A (well-ordered) | 1.4 s | 110 ms | Both fast; A is the developer's friendly path |
| B (lookup-first) | 8.5 s | 110 ms | Mongo pays the cost of unfiltered lookup |
| C (facet-wrapped) | 12+ s | 110 ms | $facet boundary kills any pushdown |
| Oracle (any of A/B/C-equivalent SQL phrasings) | 110 ms ±5 ms | (n/a) | CBO invariant |

| Prediction | Confidence |
|------------|------------|
| Mongo Variant B ≥ 4× Variant A | High |
| Mongo Variant C ≥ 5× Variant A | High |
| Oracle latency variance across SQL phrasings ≤ 10% | Very high |
| Oracle plan_hash_value identical across SQL phrasings | Very high |

## Pass/fail criteria

- **Strong pass:** Mongo Variant C ≥ 5× Variant A AND Oracle plan_hash identical across SQL phrasings.
- **Pass:** Mongo Variant B ≥ 3× Variant A.
- **Fail:** Variants converge on Mongo (suggests Mongo's optimizer is more capable than the article claims; *that's* a finding worth reporting).

## Failure modes

- Variant C may produce a result that doesn't deduplicate correctly because of `$facet`+`$unwind` mechanics. Verify equivalence rigorously before timing.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| Variant | A, B, C | Primary sweep |
| `match_selectivity` | 1%, 5%, 25% | Low selectivity should make Variant B vs A gap *larger* (more wasted lookup work) |
| Oracle SQL phrasing | 4 different orderings | Confirms CBO invariance |
| `OPTIMIZER_FEATURES_ENABLE` | `26.0.0`, `19.1.0` | Ablation: does the gap exist on older optimizer versions too? |
