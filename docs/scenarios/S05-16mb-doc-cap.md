# S05 — 16 MiB document cap

## Hypothesis

A `$group` stage with a `$push` accumulator that collects per-group records into an array can produce a single output document exceeding 16 MiB — at which point MongoDB errors with `BSONObjectTooLarge`. The cap is on the BSON document at the group's output, not on the working set, so `allowDiskUseByDefault` does not rescue this failure mode. There is no incremental escape hatch via `$out`/`$merge` — they enforce the same per-document cap. The only fix is query rewrite.

The Oracle equivalent uses `JSON_ARRAYAGG` (or `LISTAGG`) over a CLOB, with a 4 GiB per-row cap that is operationally infinite for this workload. **Expected: Mongo errors on ≥ 18/20 iterations at the configured scale; Oracle succeeds.**

## Article claim mapping

- Claim 4: 16 MiB BSON cap aborts pipelines whose intermediate `_id` accumulator exceeds it; `$out`/`$merge` doesn't chunk around this.

## Data dependencies

- Scale factor: SF1 with `--include-extension=S05` (loads the hot-customer extension: 20 customers each with 800 orders × 30 line items, with full `attrs` snapshot per line item).
- After the extension, hot customers have ~24 K line items each; grouping by customer with `$push: { product_id, quantity, extended_price, attrs }` produces ~22–30 MiB per accumulator. The 16 MiB cap is per-output-document, so this is sufficient — no need for million-row scale.

## Indexes

- `IX_ORD_CUST_DATE` — both sides.
- `IX_PROD_PK` — both sides.

## Workload — MongoDB

```javascript
db.orders.aggregate([
  { $match: {
      customer_id: { $gte: 100001, $lte: 100020 },       // 20 hot customers
      order_date: { $gte: ISODate("2024-01-01") }
  }},
  { $unwind: "$line_items" },
  { $group: {
      _id: "$customer_id",
      total_revenue: { $sum: "$line_items.extended_price" },
      // The 16 MiB-buster: collect every line item with full attrs
      line_items: { $push: {
          product_id: "$line_items.product_id",
          quantity: "$line_items.quantity",
          extended_price: "$line_items.extended_price",
          attrs: "$line_items.attrs"
      }}
  }}
])
```

Expected behavior: the pipeline begins executing, accumulates ~6–10 MB worth of line items per `_id`, then crosses 16 MiB and errors.

```
{
  "ok": 0,
  "errmsg": "BSONObj size: 17389042 (0x10A2632) is invalid. Size must be between 0 and 16793600(16MB)",
  "code": 17419,
  "codeName": "BSONObjectTooLarge"
}
```

## Workload — Oracle

```sql
WITH hot_lines AS (
  SELECT
    JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
    li.product_id,
    li.quantity,
    li.extended_price,
    li.attrs
  FROM orders_doc o,
       JSON_TABLE(o.payload, '$.line_items[*]'
         COLUMNS (
           product_id NUMBER PATH '$.product_id',
           quantity NUMBER PATH '$.quantity',
           extended_price NUMBER PATH '$.extended_price',
           attrs CLOB FORMAT JSON PATH '$.attrs'
         )) li
  WHERE JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) BETWEEN 100001 AND 100020
    AND JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= DATE '2024-01-01'
)
SELECT
  customer_id,
  SUM(extended_price) AS total_revenue,
  JSON_ARRAYAGG(
    JSON_OBJECT(
      'product_id' VALUE product_id,
      'quantity' VALUE quantity,
      'extended_price' VALUE extended_price,
      'attrs' VALUE attrs FORMAT JSON
    )
    ORDER BY product_id
    RETURNING CLOB
  ) AS line_items
FROM hot_lines
GROUP BY customer_id;
```

`RETURNING CLOB` is the critical piece — without it `JSON_ARRAYAGG` defaults to `VARCHAR2(4000)` and would also fail (with `ORA-40459`). With CLOB, each row's aggregated array can grow to 4 GiB.

Expected plan: hash group-by with workarea-managed CLOB temp segment. Oracle handles the 24 MiB-per-row CLOB without complaint.

## Verification of equivalence

If Mongo errors on every iteration (the predicted case), there is **no equivalence to verify** — that's the whole point. The run record marks this scenario as `equivalence: not-applicable; mongo-failure-expected: true`.

If Mongo succeeds on some iterations (unexpected — perhaps the harness reduced data scale), the line_items arrays must be sorted (by `product_id` on both sides — done in Oracle via `ORDER BY product_id` in `JSON_ARRAYAGG`; on Mongo via post-processing). Then hash the JSON-canonicalized array.

## Predictions

| Prediction | Confidence |
|------------|------------|
| Mongo errors on ≥ 18/20 iterations with `BSONObjectTooLarge` (code 17419) | Very high |
| Oracle succeeds on 20/20 iterations | Very high |
| Mongo per-iteration time before error: 1.5–4 seconds (proportional to data scanned before the cap is hit; smaller at SF1 than at larger scales) | Medium |
| Oracle median: 1.2–3 seconds | Medium |
| `$out` to a target collection still errors — verify via `S05-out` variant | High |
| `$merge` with upsert still errors — verify via `S05-merge` variant | High |

## Pass/fail criteria

- **Pass:** Mongo error rate ≥ 90%; Oracle success rate = 100%; the `BSONObjectTooLarge` error code (17419) is observed exactly.
- **Soft pass:** Mongo errors on most iterations but with a different error code (e.g., `OperationFailed` from a downstream stage) — investigate whether the workload tripped a different limit; document and rewrite to specifically target 16 MiB.
- **Fail (predicted invalid):** Mongo succeeds. Either the data scale is wrong (extension didn't load) or the aggregation produced smaller-than-predicted documents — debug and re-scope.

## Failure modes

This entire scenario is a designed failure mode (F1 in `09-failure-modes.md`). The pass condition is *failure on Mongo, success on Oracle*.

## Variations / sweep parameters

| Variant | Description |
|---------|-------------|
| `S05-base` | The base scenario above. |
| `S05-out` | Append `{ $out: "hot_customer_summary" }` as the terminal stage. Predicted to still fail with `BSONObjectTooLarge`. |
| `S05-merge` | Append `{ $merge: { into: "hot_customer_summary", whenMatched: "replace" } }`. Predicted to still fail. |
| `S05-rewrite-bucket` | Pre-`$bucket` partitioning of line_items by product_id ranges, per-bucket `$group`, then `$unionWith`. The "documented workaround" — measures its cost. Multi-second; not equivalent in atomicity. |
| `S05-rewrite-twoPass` | Two `$out` passes — first stage emits per-customer-per-product summaries, second stage rolls up. Atomicity-preserving but slow. |
| Oracle `S05-listagg` | Replace `JSON_ARRAYAGG` with `LISTAGG` to verify `LISTAGG` similarly handles >4000-byte CLOB. |

The headline result: **Mongo cannot complete this query without rewriting it; Oracle completes it natively in seconds.** The variant runs quantify the cost of each documented workaround so a Mongo user has a calibrated expectation of the rewrite tax.
