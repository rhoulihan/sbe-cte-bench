# 04 — Indexes

## The parity rule

Every scenario specifies indexes for both sides. The rule: **what you index on one side, you must index on the other, unless the system genuinely cannot index that path.** Asymmetric indexing is the single most common way JSON-vs-SQL benchmarks deceive readers — typically by indexing the SQL path expression carefully and leaving the MongoDB collection with only `_id`.

If a scenario benchmarks `WHERE customer_id = ? AND order_date >= ?`, both sides need a compound index on `(customer_id, order_date)`. If a scenario benchmarks deep JSON access at `payload.metadata.marketing.campaigns[*].id`, both sides need an index on that path — JSON Search Index or function-based on Oracle, multikey or wildcard on Mongo.

## Index-parity audit table

The audit table below is the master inventory; each scenario's spec inherits from it and adds scenario-specific extras.

| Index ID | MongoDB | Oracle | Used by scenarios |
|----------|---------|--------|-------------------|
| `IX_CUST_PK` | `_id` (default) | `customers (customer_id)` PK | all |
| `IX_CUST_REGION` | `customers.region_id` | `customers (region_id)` | S03, S09 |
| `IX_PROD_PK` | `_id` | `products (product_id)` PK | all |
| `IX_PROD_CAT` | `products.category_id` | `products (category_id)` | S07, S09 |
| `IX_PROD_SKU` | `products.sku` unique | `products (sku)` unique | S01, S10 |
| `IX_CAT_PK` | `_id` | `categories (category_id)` PK | all |
| `IX_CAT_PARENT` | `categories.parent_id` | `categories (parent_id)` | S07 |
| `IX_REG_PK` | `_id` | `regions (region_id)` PK | all |
| `IX_SUP_PK` | `_id` | `suppliers (supplier_id)` PK | all |
| `IX_ORD_PK` | `_id` | `orders_doc (order_id)` PK + `orders_rel (order_id)` PK | all |
| `IX_ORD_CUST_DATE` | `orders.customer_id, orders.order_date` | `JSON_VALUE(payload, '$.customer_id')` + `JSON_VALUE(payload, '$.order_date')` function-based, plus same on `orders_rel` | S02, S03, S09, S10 |
| `IX_ORD_DATE` | `orders.order_date` | function-based on `JSON_VALUE(payload, '$.order_date' RETURNING DATE)` | S02, S08 |
| `IX_ORD_LI_PRODUCT` | `orders.line_items.product_id` (multikey) | multivalue index on `payload, '$.line_items[*].product_id'` | S07, S09 |
| `IX_ORD_STATUS` | `orders.status` | function-based on `JSON_VALUE(payload, '$.status')` | S01 |

When an Oracle JSON Search Index is used, the index is created with `INDEX … ON orders_doc (payload) INDEXTYPE IS CTXSYS.JSON_SEARCH_INDEX PARAMETERS('SYNC (ON COMMIT)')` so it reflects writes immediately — fairer comparison to Mongo's synchronous index updates.

## Index choices the spec deliberately does *not* make

- **No covering indexes that materialize denormalized values.** A covering index on `(region_id, total_amount, customer_id)` would pre-stage the answer to S09 and turn it into a covered scan on both sides. It also turns a *pipeline architecture* benchmark into an *index physics* benchmark. Out of scope for v1.0.
- **No materialized views on the Oracle side that pre-aggregate.** A materialized view of `(customer_id, sum_amount_90d)` defeats the purpose of measuring CTE planning. The article concedes Oracle has materialized views; the benchmark does not use them.
- **No Atlas Search indexes.** They're real and useful, but they'd shift the comparison from aggregation-pipeline architecture to Lucene-vs-OSON — a different benchmark.
- **No partial / filtered indexes** unless a scenario explicitly calls for them. They would let one side cherry-pick the predicate selectivity.
- **No bitmap indexes on Oracle.** They'd give Oracle an unfair advantage on low-cardinality predicates the MongoDB index cannot match. Scenarios that benefit from low-cardinality grouping rely on the CBO's cost-based hash-aggregate without index assist.

## Verifying index parity per scenario

Before a scenario's timing run begins, the harness emits an index manifest:

```yaml
scenario: S03
mongo_indexes:
  - { ns: "bench.orders", spec: { customer_id: 1, order_date: 1 }, name: "IX_ORD_CUST_DATE" }
  - { ns: "bench.orders", spec: { status: 1 }, name: "IX_ORD_STATUS" }
oracle_indexes:
  - { table: "orders_doc", expression: "JSON_VALUE(payload, '$.customer_id' RETURNING NUMBER), JSON_VALUE(payload, '$.order_date' RETURNING DATE)", name: "IX_ORD_CUST_DATE_ORA" }
  - { table: "orders_doc", expression: "JSON_VALUE(payload, '$.status')", name: "IX_ORD_STATUS_ORA" }
```

This manifest is committed to the run record. Any reviewer can verify, after the fact, that index parity held. A scenario whose manifest shows asymmetric indexing is automatically invalid.

## Verifying the index actually got used

After warmup but before measurement:

- MongoDB: `db.orders.aggregate(pipeline, { explain: "executionStats" })`. The `winningPlan` must reference the expected index name. If `COLLSCAN` shows up where an `IXSCAN` is expected, the scenario fails fast and the cause is investigated.
- Oracle: `EXPLAIN PLAN FOR <SQL>` followed by `SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY)`. The plan must reference the expected index. `TABLE ACCESS FULL` where an indexed access path is expected fails the scenario.

The "expected index" is declared in each scenario spec. Sometimes the right thing is `COLLSCAN` / `TABLE ACCESS FULL` (e.g., S04 deliberately scans the whole orders collection); the scenario declares that explicitly.

## When the engines genuinely cannot match

Three places where exact parity is impossible and the scenario documents the gap:

1. **`$graphLookup` recursive lookups (S07).** MongoDB's recursive traversal uses the multikey index on the recursive field; Oracle's recursive CTE uses standard B-tree indexes on the parent column. Different access mechanics for the same logical operation. This is intentional — the scenario *measures* the difference.
2. **JDV reads (S06, S14).** A JDV is exposed as a view over base tables; "indexing the JDV" means indexing the base tables and the CBO chooses. There is no Mongo equivalent. The scenario notes this and runs both Oracle paths (OSON + JDV) for comparison against the single Mongo path.
3. **`$search` / `$searchMeta`.** Atlas Search is excluded from v1.0 (see above). For an Atlas-Search-enabled comparison, see future revisions.

In all three cases the scenario's "verification" section states the gap explicitly and the result table flags the relevant rows.
