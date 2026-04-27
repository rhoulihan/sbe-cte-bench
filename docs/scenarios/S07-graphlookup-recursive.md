# S07 — Recursive traversal: `$graphLookup` vs recursive CTE

## Hypothesis

`$graphLookup` is classic-engine only — there's no SBE pushdown for it. Each recursive iteration materializes BSON documents, allocates heap, and re-enters the engine for the next level. Oracle's recursive CTE is a single iterator construct that streams rows through the recursion with a hash anti-join for cycle detection. The architectural difference makes itself most visible at deep recursion (4–7 levels) and large fan-out (high branching factor at each level).

S07 has **two topology variants**:

- **S07-unsharded**: standard topology, `categories` collection unsharded. Tests the per-iteration classic-engine cost on its own.
- **S07-sharded**: sharded topology (the same 2-container topology built for S06), `categories` sharded on `category_id` hashed. Tests the *compounding* of the classic-engine cost with per-iteration scatter-gather. Each level of the recursion issues a fresh scatter-gather across both shards. The cost should multiply with depth × shard count.

**Expected ratio:**

- Unsharded: 5× – 25× in favor of Oracle, depending on tree depth.
- Sharded: 25× – 200× in favor of Oracle. The article doesn't call this out specifically, but it's the natural extension of S06's claim 5 to recursive workloads — and it's the worst architectural cliff in the benchmark.

## Article claim mapping

- Claim 6: `$graphLookup` is classic-only.
- Claim 5 (extended to recursion): the sharded variant exercises the scatter-gather fallback at every recursive iteration.

## Data dependencies

- Scale factor: SF1.
- The synthetic `categories` table has a self-referential 4-level taxonomy (root → top-level → mid-level → leaf). Each non-leaf has an average fan-out of 8 children. Roots: 5; total categories: ~5,000.
- Workload variants extend the depth (4 → 6 → 8) and fan-out via auxiliary `categories_deep` data.
- For **S07-sharded**: the 5,000-category collection is sharded on `category_id` hashed across the 2-shard topology built for S06. Children of a parent end up scattered across both shards (because hashed sharding distributes rows independent of the parent_id field), maximizing the recursive scatter-gather cost.

## Indexes

- `IX_CAT_PK` — both sides.
- `IX_CAT_PARENT` — both sides.
- `IX_PROD_CAT` — both sides.

## Workload — MongoDB

The query: for each root category, find all descendant categories at any depth, plus the count of products in each descendant.

```javascript
db.categories.aggregate([
  { $match: { parent_id: null } },           // SBE ✅ — root categories only

  { $graphLookup: {                          // ❌ classic only
      from: "categories",
      startWith: "$category_id",
      connectFromField: "category_id",
      connectToField: "parent_id",
      as: "descendants",
      maxDepth: 6,
      depthField: "depth"
  }},

  { $unwind: "$descendants" },               // classic (after $graphLookup)

  { $lookup: {                               // classic
      from: "products",
      localField: "descendants.category_id",
      foreignField: "category_id",
      as: "products"
  }},

  { $group: {                                // classic
      _id: { root: "$_id", desc: "$descendants.category_id" },
      depth: { $first: "$descendants.depth" },
      product_count: { $first: { $size: "$products" } }
  }}
])
```

Expected explain: `$cursor` wrapping after `$match`. Everything from `$graphLookup` onward is classic. Inside `$graphLookup`, each recursion step issues an indexed lookup on `parent_id` and materializes intermediate result documents.

## Workload — Oracle

```sql
WITH category_tree (root_id, category_id, depth) AS (
  -- anchor: root categories
  SELECT category_id AS root_id, category_id, 0 AS depth
  FROM categories
  WHERE parent_id IS NULL

  UNION ALL

  -- recursive: descend by parent_id
  SELECT t.root_id, c.category_id, t.depth + 1
  FROM category_tree t
  JOIN categories c ON c.parent_id = t.category_id
  WHERE t.depth < 6
),
descendant_with_products AS (
  SELECT
    ct.root_id,
    ct.category_id,
    ct.depth,
    COUNT(p.product_id) AS product_count
  FROM category_tree ct
  LEFT JOIN products p ON p.category_id = ct.category_id
  GROUP BY ct.root_id, ct.category_id, ct.depth
)
SELECT * FROM descendant_with_products
ORDER BY root_id, depth, category_id;
```

Expected plan: recursive CTE with `CONNECT BY` semantics under the hood; hash anti-join for cycle detection if cycles are possible (they aren't, but the plan accommodates). LEFT JOIN with `products` is a hash join.

## Verification of equivalence

Sort by `(root_id, depth, category_id)`, hash. `product_count` exact integers; matches must be byte-equal.

## Predictions

### S07-unsharded (standard topology)

| depth | Predicted Mongo median | Predicted Oracle median | Ratio |
|-------|------------------------|-------------------------|-------|
| 4 | 480 ms | 95 ms | 5.0× |
| 5 | 850 ms | 130 ms | 6.5× |
| 6 | 1.6 s | 180 ms | 8.9× |
| 8 | 6.5 s | 320 ms | 20×+ |

### S07-sharded (sharded topology, categories sharded across 2 shards)

| depth | Predicted Mongo median | Predicted Oracle median | Ratio |
|-------|------------------------|-------------------------|-------|
| 4 | 4.5 s | 95 ms | 47× |
| 5 | 11 s | 130 ms | 85× |
| 6 | 28 s | 180 ms | 156× |
| 8 | 180 s+ (or timeout) | 320 ms | ≥ 560× |

The sharded variant's predicted blowup comes from the multiplicative composition of two architectural costs: (a) `$graphLookup` is classic-engine, so each recursion iteration pays per-document materialization; (b) the foreign collection is sharded, so each recursion iteration also pays scatter-gather per local doc per shard. At depth 8 with branching factor 8, that's 8^8 = 16.7 M document-iteration combinations, each with an independent remote cursor. We expect this to time out at depth 8.

(Times scaled to SF1 = 1 M orders / 100 K customers / ~5 K categories. Ratios are the architectural finding; absolute times scale with the recursion combinatorics.)

| Prediction | Confidence |
|------------|------------|
| Mongo `$graphLookup` time grows super-linearly with depth (unsharded) | High |
| Mongo `$graphLookup` time grows *combinatorially* with depth (sharded) | Very high |
| Sharded variant ≥ 5× slower than unsharded variant at same depth | Very high |
| Sharded depth=8 may time out (≥ 5 min) | Medium-high |
| Oracle recursive CTE grows roughly linearly with rows-emitted (Oracle path identical between variants — categories table is the same single-instance table) | High |
| Mongo explain shows `$cursor` boundary right after `$match` | Very high |
| Oracle plan shows recursive `WITH` with hash-anti-join cycle prevention | High |

## Pass/fail criteria

### S07-unsharded
- **Strong pass:** Ratio at depth=6 ≥ 10× AND ratio grows monotonically with depth.
- **Pass:** Ratio at depth=6 ≥ 5× AND classic-engine confirmed for `$graphLookup`.
- **Fail:** Ratio < 3× — investigate (cardinality may be too low at SF1; use the `_deep` extension to amplify).

### S07-sharded
- **Strong pass:** Sharded depth=6 ratio ≥ 50× over Oracle baseline AND sharded-vs-unsharded gap at same depth ≥ 5×.
- **Pass:** Sharded depth=6 ratio ≥ 25×.
- **Fail (test invalid):** Mongo `$graphLookup` errors with "operation not supported on sharded collection" on the version being tested. Document the version-specific behavior and skip this variant. (As of 8.x, `$graphLookup` against a sharded foreign IS supported, but it has been historically restricted.)

## Failure modes

- **Out-of-memory at depth=8.** The Mongo aggregator materializes the descendants array per root; with high fan-out at depth 8, a single document might exceed available memory mid-stage. If this happens, record it as a memory-pressure failure and continue.
- **Timeout on S07-sharded at depth ≥ 6.** The combinatorial blowup of (classic per-iteration) × (per-iteration scatter-gather) × (depth × fan-out) can produce iterations that take many minutes. The harness sets a per-iteration timeout of 5 min by default; raised to 30 min for S07-sharded. If iterations time out *even at the raised limit*, the timeout itself is the result — record it and report.
- **`$graphLookup` against sharded foreign restrictions.** Historically (pre-5.1) `$graphLookup` against a sharded foreign collection was unsupported. As of 8.x it is supported, but verify in the explain output that the operation actually executes rather than errors. If MongoDB on the pinned version refuses the operation, that's a finding worth reporting on its own — record it and skip the variant.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `topology` | unsharded (standard), sharded (S06 topology) | Primary architectural variant |
| `depth` | 3, 4, 5, 6, 7, 8 | Primary depth sweep |
| `fanout` | 4, 8, 16 (extension data) | Secondary sweep — confirms the ratio scales with branching |
| `restrictSearchWithMatch` | none, `{ active: true }` | Tests `$graphLookup`'s built-in filter knob; predicted to not change the boundary tax |
| Cycle detection toggle | implicit | Both engines detect cycles; not exposed as a knob |
| Reverse direction (leaf → root) | one variant | Less common but exercises the anti-cycle path the other way |

The sharded variant reuses the S06 topology — when both S06 and S07-sharded are scheduled in the same run, the sharded topology comes up once, both scenarios run, then the topology comes down.
