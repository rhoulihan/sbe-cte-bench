# 03 — Data Model

## Workload domain

A synthetic e-commerce schema, chosen because it is:

- **Recognizably realistic.** Orders, line items, customers, products, regions, suppliers — every database benchmark of the last 30 years uses some variant. Familiarity helps reviewers spot incorrect modeling.
- **Naturally hierarchical.** Orders contain line items contain product references. Document-shaped storage is genuinely useful for some access patterns.
- **Naturally relational.** Customers, products, regions, suppliers are dimension entities reused across orders. Relational storage is genuinely useful for some access patterns.
- **Aggregation-rich.** Almost every meaningful query is a multi-stage aggregation: revenue by region by category, top-N customers, rolling 90-day windows, basket-anomaly detection.

The schema is small enough to specify exhaustively in this document and large enough to exercise every benchmarked architectural seam.

## Entities

### `customers`

| Field | Type | Notes |
|-------|------|-------|
| `customer_id` | int | PK. 1..N. |
| `name` | string | Faker-generated. ~40 bytes. |
| `email` | string | Unique. ~30 bytes. |
| `region_id` | int | FK → regions.region_id. |
| `signup_date` | date | Uniform over 2018-01-01..2025-12-31. |
| `tier` | enum | `bronze`/`silver`/`gold`/`platinum`. Skewed: 60/25/12/3. |
| `metadata` | object | Nested object: `{ marketing: { campaigns: [...] }, prefs: {...} }`. ~500 bytes. |

### `products`

| Field | Type | Notes |
|-------|------|-------|
| `product_id` | int | PK. |
| `sku` | string | Unique. |
| `name` | string | |
| `category_id` | int | FK → categories.category_id. |
| `supplier_id` | int | FK → suppliers.supplier_id. |
| `price` | decimal(10,2) | $5..$5000, lognormal. |
| `attributes` | object | Variable nested object — depth 3–7, ~1 KB typical. |

### `categories`

| Field | Type | Notes |
|-------|------|-------|
| `category_id` | int | PK. |
| `name` | string | |
| `parent_id` | int | Nullable. Self-referential — forms a 4-level taxonomy. Used in S07 (recursive). |

### `regions`

| Field | Type | Notes |
|-------|------|-------|
| `region_id` | int | PK. ~50 regions. |
| `name` | string | |
| `country` | string | |

### `suppliers`

| Field | Type | Notes |
|-------|------|-------|
| `supplier_id` | int | PK. |
| `name` | string | |
| `country` | string | |
| `tier` | enum | `preferred`/`approved`/`probation`. |

### `orders`

The main fact table. **This is the only entity stored as JSON in MongoDB AND in Oracle.** Customers, products, categories, regions, and suppliers are stored relationally on both sides — but in MongoDB they're collections (with `_id` PK and indexes), and in Oracle they're tables. The orders document is the JSON-shaped entity where `JSON_TABLE` and OSON earn their keep on the Oracle side, and where the aggregation pipeline naturally operates on the Mongo side.

| Field | Type | Notes |
|-------|------|-------|
| `_id` / `order_id` | int | PK. |
| `customer_id` | int | FK. |
| `order_date` | date | Past 5 years, slightly skewed toward recent. |
| `status` | enum | `pending`/`shipped`/`delivered`/`cancelled`/`returned`. |
| `currency` | string | "USD"/"EUR"/"GBP"/"JPY"; mostly USD. |
| `payment` | object | `{ method, transaction_id, billing_address: {…} }`. |
| `shipping` | object | `{ method, tracking_id, address: {…} }`. |
| `line_items` | array | 1–50 items per order, lognormal. **Each line item is an object** — see below. |
| `notes` | string | Free-text, optional. 0–500 bytes. |
| `audit` | array | `{ event_at, event_type, actor }` records, 0–20 entries. |

### `orders.line_items[]` (subdocument)

| Field | Type | Notes |
|-------|------|-------|
| `line_id` | int | Unique within the order. |
| `product_id` | int | FK → products. |
| `quantity` | int | 1–20, weighted toward 1–3. |
| `unit_price` | decimal | At time of order. |
| `discount` | decimal | 0..0.40. |
| `extended_price` | decimal | `quantity * unit_price * (1 - discount)`. Pre-computed. |
| `attrs` | object | Snapshot of `products.attributes` at order time — 800 bytes typical. Inflates document size. |

A typical order document is **2.5–4 KB** at scale-factor SF1. A pathological order (50 line items, large `attrs` snapshots, many audit events) approaches **180 KB**. Scenario S05 specifically constructs orders that would, when grouped by customer with `$push`, push the per-customer accumulator past 16 MiB — possible at smaller scale because the 16 MiB cap is per-output-document, not per-collection-size.

## Storage on each side

| Side | Customers/Products/Categories/Regions/Suppliers | Orders |
|------|------|--------|
| MongoDB | Five collections with their own indexes. | One collection (`orders`) with the full JSON shape per document. |
| Oracle | Five normal relational tables. | Two representations, both populated, both indexed: (a) a JSON column `payload OSON` on `orders_doc`, with `JSON_TABLE` projections in queries; and (b) a fully normalized representation in `orders_rel` + `order_line_items_rel`, exposed through a JSON Duality View `orders_jdv`. |

The dual representation on the Oracle side is **deliberate**. Different scenarios benchmark different access models:

- Scenarios S01–S05, S08–S10 query Oracle through `JSON_TABLE` over `orders_doc.payload`. This is the closest analogue to a MongoDB pipeline reading from a single collection.
- Scenario S07 (recursive `$graphLookup` vs recursive CTE) also uses the OSON path.
- Scenarios S06, S14 (write path) use both: OSON path for the read benchmark, JDV path for the write benchmark.
- The cross-model claim in Part 8 of the article — "JDV optimized against base tables" — is verified by scenario-specific JDV measurements.

## Scale factors

Scale factors are sized to fit within **Oracle Database 26ai Free's 12 GB user-data cap per PDB** (with safety margin for indexes and system segments). MongoDB runs at the same data scale on a matched 4 GB container memory budget.

| SF | customers | products | orders | line_items (~) | orders raw | total user data (with indexes, both engines) |
|----|-----------|----------|--------|----------------|------------|----------------------------------------------|
| SF0.1 | 10 K | 1 K | 100 K | 500 K | ~300 MB | ~600 MB |
| **SF1** | **100 K** | **10 K** | **1 M** | **5 M** | **~3 GB** | **~6 GB** |
| ~~SF10~~ | — | — | — | — | — | **out of scope for v1.0** (exceeds Oracle Free's 12 GB user-data cap; document as future work for an EE-licensed run) |

**SF1 is the primary benchmark scale.** At 1 M orders + 5 M line items + ~6 GB total user data per engine, the dataset fits comfortably within Oracle Free's 12 GB cap with ~6 GB of headroom. The working-set for most queries fits within the 1.5 GB WT cache / 1.2 GB SGA budgets — which is the right zone for measuring engine architecture rather than IO ceilings. SF0.1 exists for fast iteration during scenario authoring (the entire load takes ~2 minutes; one full scenario sweep ~10 minutes).

### Why SF1 is small enough to be a "real" benchmark

A 1 M-order dataset is small by warehouse standards, but **it is large enough to expose every architectural phenomenon under test**:

- Pipelines with 100 MB+ working sets (S04) require deliberate workload composition, not raw scale — at 1 M orders × 5 M line items, an unfiltered `$unwind` + `$group` produces tens-of-MB intermediate states; the S04 scenario adds the deep-skew extension on top to push past 100 MB.
- 16 MiB document accumulators (S05) require ~16 K records per group at ~1 KB each — the S05 hot-customer extension provides this.
- SBE→classic boundary tax (S03) is per-row × per-stage — visible at any data scale.
- Sharded `$lookup` scatter-gather (S06) scales with local-document count — visible at thousands of local docs, not millions.

The scale-invariant phenomena are exactly the ones the article is making claims about. We're not benchmarking IO throughput at terabyte scale; we're benchmarking the architecture of multi-stage aggregation.

> Out of scope: storage-format primitive comparisons (BSON length-prefix
> scan vs OSON hash-indexed navigation). Both engines materialize JSON
> values into a mutable in-memory representation before the SQL/aggregation
> evaluator sees them, so per-row dispatch dominates the storage-primitive
> delta at this layer. The article's CPU-microbenchmark figures (28× /
> 529×) belong in a dedicated harness, not here.

## Generators

A single Python module `harness/data/generator.py` (TBD) emits both the MongoDB BSON and the Oracle insert-friendly form from a single source of truth. The generator:

1. Takes `seed` (default `0xCAFE_F00D_BEEF_5BE`) and `scale_factor` arguments.
2. Uses a deterministic PRNG (`numpy.random.Generator(PCG64(seed))`) — never `random.random()`.
3. Generates entities in dependency order: regions → categories → suppliers → products → customers → orders.
4. Streams output to `data/generated/{customers,products,…}.{bson,csv}` files. BSON for Mongo via `bson.encode()`. CSV for Oracle via SQL*Loader-compatible format with explicit JSON column for `orders_doc.payload`.
5. Hashes the final output. The hash is recorded with the run record so anyone reproducing the benchmark can verify their data is byte-stable.

**Determinism is non-negotiable.** Two runs of the generator with the same seed and SF must produce byte-identical output. This is verified in CI (TBD).

## Loading

| Side | Method |
|------|--------|
| MongoDB | `mongorestore --bsonOnly --bypassDocumentValidation` from the generated `.bson` files. ~90 seconds for SF1 on a 2-CPU/4 GB container (laptop-class). |
| Oracle | SQL*Loader with `DIRECT=TRUE` for relational entities. JSON column loaded as `BLOB` with explicit OSON conversion via `INSERT … SELECT JSON_TRANSFORM(…) FROM staging`. ~3 minutes for SF1. (Oracle Free's 2-CPU cap means parallel load degree is limited to 2.) |

After load, both sides run their respective stat-update commands (`db.collection.reIndex()` is *not* needed; `DBMS_STATS.GATHER_TABLE_STATS` *is* needed for Oracle). Stats are gathered with `METHOD_OPT => 'FOR ALL COLUMNS SIZE AUTO'` and `CASCADE => TRUE`.

## Documents that intentionally violate constraints

Some scenarios — S05 (16 MiB cap), S04 (100 MB working set) — require the data to *force* a specific failure mode. These scenarios specify a *parameterized data extension* on top of the base SF1 dataset:

- **S05 hot-customer extension.** A small set of synthetic customers (20, with `customer_id` ≥ 100_001) are given 800 orders each, each with 30 line items containing the full `attrs` snapshot. Total extension data: ~480 K line items, ~600 MB. Grouping by customer with `$push` produces per-customer accumulators of ~22–30 MiB — guaranteed to trip `BSONObjectTooLarge`. The 16 MiB cap is per-output-document; smaller dataset is sufficient.
- **S04 deep-skew extension.** 2 K artificial product categories with deliberately repeated cardinality so that `$group` over category emits ~200 K rows of intermediate state with `$addToSet` accumulators sized to push the working set across the 100 MB cap. Total extension data: ~800 MB.

These extensions are off by default; loaded only when the corresponding scenario runs. Generator flag: `--include-extension=S04,S05`. With both extensions plus the SF1 base, total user data ≈ 7.5 GB — still well within Oracle Free's 12 GB cap.
