# S01 — Baseline scan + filter + project

## Hypothesis

A trivial pipeline (filter on indexed field, project a small set of fields, return) runs at comparable speed on both engines. The scenario calibrates the noise floor of the harness and confirms that neither system has a structural advantage on the simplest possible workload. **Expected ratio: 0.8× – 1.3×** (within noise).

## Article claim mapping

None directly. Calibration scenario.

## Data dependencies

- Scale factor: SF1.
- No extension flags.

## Indexes

| Index ID | MongoDB | Oracle |
|----------|---------|--------|
| `IX_ORD_STATUS` | `{ status: 1 }` | function-based on `JSON_VALUE(payload, '$.status')` |
| `IX_ORD_DATE` | `{ order_date: 1 }` | function-based on `JSON_VALUE(payload, '$.order_date' RETURNING DATE)` |

## Workload — MongoDB

```javascript
db.orders.aggregate([
  { $match: {
      status: "delivered",
      order_date: { $gte: ISODate("2025-01-01"), $lt: ISODate("2025-04-01") }
  }},
  { $project: {
      _id: 0,
      order_id: 1,
      customer_id: 1,
      order_date: 1,
      total_amount: { $sum: "$line_items.extended_price" }
  }}
])
```

Two stages, both SBE-eligible. The `$project` stage's `$sum` over `line_items.extended_price` exercises the `extended_price` field of each item but does not unwind. ~80 K rows match the predicate at SF1.

Expected explain: SBE all the way; `IXSCAN` on `IX_ORD_DATE` (or the compound `(status, order_date)` if Mongo prefers it after stats); no `$cursor` wrapping.

## Workload — Oracle

```sql
SELECT
  JSON_VALUE(payload, '$.order_id' RETURNING NUMBER) AS order_id,
  JSON_VALUE(payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
  JSON_VALUE(payload, '$.order_date' RETURNING DATE) AS order_date,
  (
    SELECT SUM(jt.extended_price)
    FROM JSON_TABLE(
      o.payload, '$.line_items[*]'
      COLUMNS (extended_price NUMBER PATH '$.extended_price')) jt
  ) AS total_amount
FROM orders_doc o
WHERE JSON_VALUE(payload, '$.status') = 'delivered'
  AND JSON_VALUE(payload, '$.order_date' RETURNING DATE) >= DATE '2025-01-01'
  AND JSON_VALUE(payload, '$.order_date' RETURNING DATE)  < DATE '2025-04-01';
```

The correlated `JSON_TABLE` evaluates per outer row but participates in the row-source pipeline. ~80 K rows match.

Expected plan: index range scan on `IX_ORD_DATE_ORA`, table access by ROWID, lateral `JSON_TABLE` evaluation per row, no temp segment usage.

## Verification of equivalence

Result rows are sorted by `order_id` and hashed. Hash must match on both sides. Total amount is a `NUMBER` on Oracle and a `Decimal128` (or double) on Mongo — comparison uses relative tolerance `1e-9`.

## Predictions

| Prediction | Confidence | Rationale |
|------------|------------|-----------|
| Median ratio MongoDB / Oracle ∈ [0.8, 1.3] | High | Both engines fully indexed; both SBE/CBO-friendly; ~80 K rows is too small to expose architectural differences. |
| MongoDB explain shows no `$cursor` wrapper | High | Pipeline is entirely SBE-eligible. |
| Oracle plan shows no `TEMP TABLE TRANSFORMATION` | High | No multi-reference CTE. |
| Both engines `cv < 0.05` after warmup | High | Index range scan + small projection; deterministic plan. |
| Neither engine spills | High | ~80 K rows × small row size easily fits. |

## Pass/fail criteria

- **Pass:** Ratio within prediction range AND both engines complete 20/20 iterations cleanly AND equivalence hashes match.
- **Fail (test invalid):** Ratio outside [0.5, 2.0]; some other effect dominates.
- **Fail (harness invalid):** Either engine cv > 0.10 or any iteration > 3× median.

## Failure modes

None expected. If S01 fails, the harness is broken — fix the harness before running anything else.

## Variations / sweep parameters

None. S01 is a single-point calibration scenario.
