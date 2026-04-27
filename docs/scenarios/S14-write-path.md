# S14 — Write path: `$merge` vs `MERGE INTO`

## Hypothesis

Most aggregation comparisons stop at SELECT. The right-hand side of the pipeline — writing results back to a collection or table — is where the architectural constraints differ most sharply:

- MongoDB's `$out` is restricted to last-stage; cannot target a sharded collection; cannot run inside a transaction; subject to 16 MiB per output document; cannot appear inside `$lookup`/`$facet`/`$unionWith` sub-pipelines.
- MongoDB's `$merge` is more flexible — supports upsert, replace, custom pipelines — but inherits the same per-document 16 MiB cap and forbidden-in-transactions rule.
- Oracle's `MERGE INTO` operates as part of the same query block as the SELECT it draws from; participates in transactions; targets any table (partitioned, sharded, or otherwise); has no per-row size limit (CLOB up to 4 GiB).

**Expected:** For routine "write 100 K aggregated rows" workloads, both are within ~2× of each other. For workloads that bump into Mongo's constraints (transactional consistency, sharded targets, large per-row outputs, multi-stage write-and-read), Oracle is dramatically faster — sometimes the only option.

## Article claim mapping

- Research dimension: write-path under aggregation.

## Data dependencies

- Scale factor: SF1.
- Output target: `customer_summary` collection / `customer_summary` table.

## Indexes

- `IX_ORD_CUST_DATE` — both sides.
- `customer_summary._id` (Mongo) / PK on `customer_summary.customer_id` (Oracle).

## Workload structure

Three variants:

### V14-a: Routine batch upsert
"Daily refresh of customer revenue summaries" — runs the S02 aggregation and upserts the result into `customer_summary`.

#### MongoDB
```javascript
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-08-01") } } },
  { $group: { _id: "$customer_id", revenue: { $sum: "$line_items.extended_price" } } },
  { $merge: {
      into: "customer_summary",
      on: "_id",
      whenMatched: "replace",
      whenNotMatched: "insert"
  }}
])
```

#### Oracle
```sql
MERGE INTO customer_summary tgt
USING (
  WITH revenue_by_customer AS (
    SELECT JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER) AS customer_id,
           (SELECT SUM(li.extended_price)
            FROM JSON_TABLE(o.payload, '$.line_items[*]'
              COLUMNS (extended_price NUMBER PATH '$.extended_price')) li) AS revenue
    FROM orders_doc o
    WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= ADD_MONTHS(SYSDATE, -3)
  )
  SELECT customer_id, SUM(revenue) AS revenue
  FROM revenue_by_customer
  GROUP BY customer_id
) src
ON (tgt.customer_id = src.customer_id)
WHEN MATCHED THEN UPDATE SET tgt.revenue = src.revenue
WHEN NOT MATCHED THEN INSERT (customer_id, revenue) VALUES (src.customer_id, src.revenue);
COMMIT;
```

### V14-b: Transactional consistency required

The aggregation result must be written *atomically* with another modification — say, an audit log of the refresh. Mongo's `$merge` inside a transaction is forbidden; the workaround is two-stage execution (aggregate to a staging collection, then read from staging within the transaction). Oracle's `MERGE` is itself transactional and composes with adjacent DML.

#### MongoDB (forced workaround)
```javascript
// Stage 1: aggregate to staging (no transaction)
db.orders.aggregate([
  { $match: ... },
  { $group: ... },
  { $out: "customer_summary_staging" }
]);
// Stage 2: open transaction, read staging, write target + audit
const session = client.startSession();
session.startTransaction();
const docs = db.customer_summary_staging.find({}).toArray();
db.customer_summary.bulkWrite(docs.map(d => ({
  replaceOne: { filter: { _id: d._id }, replacement: d, upsert: true }
})), { session });
db.audit_log.insertOne({ event: "refresh", ts: new Date() }, { session });
session.commitTransaction();
```

#### Oracle (single transaction)
```sql
BEGIN
  MERGE INTO customer_summary tgt USING (...) src ON (...)
    WHEN MATCHED THEN UPDATE SET ...
    WHEN NOT MATCHED THEN INSERT (...);
  INSERT INTO audit_log (event_type, event_at) VALUES ('refresh', SYSTIMESTAMP);
  COMMIT;
END;
/
```

### V14-c: Sharded target

The target `customer_summary` is sharded across the **2-shard topology defined for S06** (`mongo-bench-shard1-router` + `mongo-bench-shard2`), with `customer_id` as the hashed shard key. Mongo's `$out` cannot target a sharded collection; `$merge` *can* (since 4.2). Oracle's MERGE works against any partitioned table.

V14-c reuses the S06 sharded topology — the harness brings it up once when both S06 and V14-c are scheduled in the same run, runs S06 and V14-c, then tears it down.

#### MongoDB
```javascript
db.orders.aggregate([..., {
  $merge: { into: "customer_summary", on: "_id", whenMatched: "replace", whenNotMatched: "insert" }
}])
```

`$out` against the sharded target would fail with `OperationFailed: $out is not supported for sharded collection`. We measure `$merge` as the only viable option.

#### Oracle
Standard MERGE against a HASH-partitioned `customer_summary` table; no special handling.

## Verification of equivalence

After both engines complete the write, query `customer_summary` (or `customer_summary` table) and compare full contents — same number of rows, same revenue values per customer (relative tolerance `1e-9`), no orphan rows, no missing rows.

## Predictions

| Variant | Predicted Mongo median | Predicted Oracle median | Notes |
|---------|------------------------|-------------------------|-------|
| V14-a (routine) | 1.8 s | 940 ms | Mongo bulk-update overhead vs single-statement MERGE |
| V14-b (txn consistency, Mongo workaround) | 4.2 s | 1.05 s | Mongo's two-stage workaround pays a 2× tax |
| V14-c (sharded target) | 6.5 s | 1.1 s | Sharded target adds scatter cost; Oracle's partitioned MERGE doesn't |

| Prediction | Confidence |
|------------|------------|
| V14-b Mongo workaround ≥ 2× V14-a Mongo | High |
| V14-c Mongo `$merge` to sharded target shows scatter pattern in mongos slow log | High |
| Oracle invariant within 30% across V14-a/b/c | High |

## Pass/fail criteria

- **Pass:** V14-b Mongo workaround ≥ 1.8× V14-a; Oracle invariant ≤ 1.5×.
- **Strong pass:** V14-c Mongo ≥ 5× V14-c Oracle.

## Failure modes

- **Sharded topology required for V14-c.** Reuses the S06 topology; if the S06 topology fails to come up (see `S06-lookup-sharded.md` failure modes), V14-c is skipped with the same warning. V14-a and V14-b run on the standard topology and are unaffected.
- **Idempotency.** Re-running the same scenario on the same data must produce the same target state. The harness verifies this by running V14-a twice in succession; the second run should be logically a no-op (well, an UPDATE) and finish in similar time.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| Variant | a, b, c | Primary |
| `target_size_existing_rows` | 0%, 50%, 100% pre-populated | Tests whenMatched=replace cost vs whenNotMatched=insert cost |
| `whenMatched` (Mongo) | replace, merge, [pipeline] | The richer `$merge` modes carry more overhead |
| Cold cache | yes/no | |
