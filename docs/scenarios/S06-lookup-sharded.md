# S06 — `$lookup` against sharded foreign collection

## Hypothesis

A `$lookup` stage whose `from` collection is sharded falls back to classic-engine execution (per `sbe_pushdown.cpp` r8.2.2: `isAnySecondaryNamespaceAViewOrNotFullyLocal()`). The fallback issues a per-local-document remote cursor on each shard, scatter-gathering responses and joining in memory. Latency scales as `(local docs) × (shard count × remote-cursor cost)`.

The Oracle equivalent — a hash join across two large tables — does not have a sharded analogue at Oracle Free's caps; the test instead measures a single-instance hash join under matched data scale, demonstrating the *absence* of the architectural cliff. This is the architectural point of the scenario: with the shared-storage / single-engine model, joining a "large second table" doesn't get worse — there is no shard fan-out to begin with.

**Expected ratio: 10× – 50×** in favor of Oracle, depending on shard count and matched local-document count. This is one of the largest gaps in the benchmark.

## Article claim mapping

- Claim 5: Sharded foreign `$lookup` falls back to classic scatter-gather.

## MongoDB topology for S06

S06 introduces the sharded topology. **Three scenarios use this topology**:

- **S06** (this scenario) — `$lookup` against sharded foreign customers.
- **S07-sharded** (in `S07-graphlookup-recursive.md`) — `$graphLookup` against sharded foreign categories. The same architectural cliff applied recursively.
- **S14-V14c** (in `S14-write-path.md`) — `$merge` writing to a sharded target.

When any of these scenarios is scheduled, the harness shuts down the standard topology, brings up the sharded topology, runs all sharded-topology scenarios as a group, then tears down the sharded topology and restarts the standard one. The cluster initialization is performed once per topology lifecycle.

All `mongod` instances in the sharded topology — `cfgRS`, `shard1`, `shard2` — run as **single-node replica sets with journaling enabled**, identical configuration to the standard topology's mongod. No standalone mongod, anywhere in the benchmark.

### Two-container sharded cluster

To minimize footprint while still triggering the SBE→classic fallback, the topology uses **2 Docker containers**:

```
┌────────────── host ──────────────────────────────────────────────────┐
│                                                                       │
│  ┌─ mongo-bench-shard1-router ─────────────────┐                      │
│  │ --cpus="2.0"  --memory="4g"                 │                      │
│  │ --cpuset-cpus="0-1"                         │                      │
│  │   ▸ mongod  --shardsvr --replSet=shard1     │  port 27018          │
│  │   ▸ mongod  --configsvr --replSet=cfgRS     │  port 27019          │
│  │   ▸ mongos  --configdb=cfgRS/...:27019      │  port 27017 ◀─ harness
│  └─────────────────────────────────────────────┘                      │
│                                                                       │
│  ┌─ mongo-bench-shard2 ────────────────────────┐                      │
│  │ --cpus="2.0"  --memory="4g"                 │                      │
│  │ --cpuset-cpus="4-5"                         │                      │
│  │   ▸ mongod  --shardsvr --replSet=shard2     │  port 27018          │
│  └─────────────────────────────────────────────┘                      │
│                                                                       │
│  ┌─ oracle-bench (unchanged) ──────────────────┐                      │
│  │ --cpus="2.0"  --memory="4g"  cpuset 2-3     │                      │
│  └─────────────────────────────────────────────┘                      │
│                                                                       │
│  ┌─ harness (cpus 6, --memory="2g") ────────────────────────────────┐ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

### Container 1 contents — `mongo-bench-shard1-router`

Three colocated processes:

1. **shard1 mongod** — `mongod --shardsvr --replSet=shard1 --port=27018 --bind_ip_all --dbpath=/data/shard1 --wiredTigerCacheSizeGB=1.0 --journalCommitInterval=100`
2. **config server mongod** — `mongod --configsvr --replSet=cfgRS --port=27019 --bind_ip_all --dbpath=/data/cfg --wiredTigerCacheSizeGB=0.25 --journalCommitInterval=100`
3. **mongos** — `mongos --configdb=cfgRS/localhost:27019 --port=27017 --bind_ip_all`

All three started by a `supervisord` (or `s6-overlay`) entrypoint inside the container. The shardsvr and configsvr each run as **single-node replica sets** (`shard1` and `cfgRS` respectively) so mongos/configsvr replica-set requirements are satisfied without multiplying nodes. Journaling is on by default in 8.x and verified by the harness pre-run.

### Container 2 contents — `mongo-bench-shard2`

Single mongod, configured as a single-node replica set:
- **shard2 mongod** — `mongod --shardsvr --replSet=shard2 --port=27018 --bind_ip_all --dbpath=/data/shard2 --wiredTigerCacheSizeGB=1.5 --journalCommitInterval=100`

### Cluster initialization (run once after both containers up)

```javascript
// in shard1 container
mongosh --port 27019 --eval 'rs.initiate({_id:"cfgRS", configsvr:true, members:[{_id:0, host:"localhost:27019"}]})'
mongosh --port 27018 --eval 'rs.initiate({_id:"shard1", members:[{_id:0, host:"shard1-host:27018"}]})'

// in shard2 container
mongosh --port 27018 --eval 'rs.initiate({_id:"shard2", members:[{_id:0, host:"shard2-host:27018"}]})'

// from harness, via mongos
mongosh --port 27017 --eval '
  sh.addShard("shard1/shard1-host:27018");
  sh.addShard("shard2/shard2-host:27018");
  sh.enableSharding("bench");
  sh.shardCollection("bench.customers", { customer_id: "hashed" });
  // orders is intentionally NOT sharded — primary shard only
'
```

After `shardCollection` with hashed shard key, `customers` chunks distribute roughly 50/50 across `shard1` and `shard2`. `orders` lives entirely on the primary shard (`shard1` by default).

### Resource asymmetry (deliberate)

The sharded MongoDB topology gets **2× the CPU and memory budget** of the Oracle Free instance for S06: 4 CPUs and 8 GB total Mongo container memory, vs. Oracle's 2 CPUs and 4 GB. This is documented in `08-fairness-charter.md` as a deliberate exception. Rationale:

- A real production sharded MongoDB cluster *does* have more resources than a single Oracle instance — that's the point of sharding. Constraining sharded Mongo to single-shard resources would be straw-manning.
- Even with the 2× resource advantage, the architectural cliff (classic-engine fallback + scatter-gather per local doc) shows up unmistakably. Resources don't fix architecture.
- If anything, giving Mongo more resources than Oracle here makes the result *more* defensible: Mongo had every advantage we could give it within the v1.0 footprint; the cliff is still there.

This is the only scenario where the resource budgets are not symmetric. Documented prominently in the writeup.

## Data dependencies

- Scale factor: SF1 (1 M orders, 100 K customers).
- 100 K customers shard-distribute roughly 50/50 across `shard1` and `shard2` (~50 K per shard).
- 1 M orders are unsharded, all on `shard1` (the primary).

## Indexes

- `IX_CUST_PK` — both sides. On Mongo: implicit on the hashed shard key. On Oracle: PK B-tree on `customers (customer_id)`.
- `IX_ORD_CUST_DATE` — both sides.

After `shardCollection`, MongoDB creates a hashed index on `customer_id` automatically. We add an additional ascending B-tree index on `customer_id` for the `$lookup`'s match condition (the hashed index alone doesn't service equality lookups efficiently for `$lookup`).

## Workload — MongoDB

```javascript
// connected to mongos at port 27017
db.orders.aggregate([
  { $match: { order_date: { $gte: ISODate("2024-01-01") } } },
  { $lookup: {
      from: "customers",      // sharded ⚠️ — triggers SBE pushdown disqualification
      localField: "customer_id",
      foreignField: "customer_id",
      as: "customer"
  }},
  { $unwind: "$customer" },
  { $project: {
      order_id: 1,
      order_date: 1,
      customer_name: "$customer.name",
      tier: "$customer.tier",
      region_id: "$customer.region_id"
  }}
])
```

The same pipeline against an *unsharded* `customers` (run on the standard single-RS topology, used as `S06-unsharded` baseline) runs in SBE end-to-end. Against the *sharded* `customers` topology, the `$lookup` falls back to classic, the SBE prefix length is 1 (just the `$match`), and per-local-document remote cursors fan out to both shards.

Expected explain (sharded variant):

- The mongos returns a `splitPipeline` shape: shard-side operations and merger operations.
- Per shard, explain shows classic-engine execution (no `EQ_LOOKUP`).
- Per-local-doc latency dominated by remote cursor round-trip + cross-shard scatter-gather.
- Slow-query log on mongos shows individual `getMore` calls per local doc per shard.

## Workload — Oracle

```sql
SELECT
  JSON_VALUE(o.payload, '$.order_id' RETURNING NUMBER) AS order_id,
  JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) AS order_date,
  c.name AS customer_name,
  c.tier,
  c.region_id
FROM orders_doc o
JOIN customers c ON c.customer_id = JSON_VALUE(o.payload, '$.customer_id' RETURNING NUMBER)
WHERE JSON_VALUE(o.payload, '$.order_date' RETURNING DATE) >= DATE '2024-01-01';
```

CBO picks hash join (small-build `customers`, large-probe `orders_doc`) — same plan as the unsharded comparison, because Oracle has no sharded analogue. No per-row remote calls.

Expected plan: HASH JOIN with `customers` on the build side, IXSCAN on `IX_ORD_DATE_ORA` on the probe side.

## Verification of equivalence

Sort by `order_id`, hash. Customer fields deterministic from the seed. ~360 K result rows at SF1 (orders since 2024-01-01).

## Predictions

| Variant | Predicted Mongo median | Predicted Oracle median | Ratio |
|---------|------------------------|-------------------------|-------|
| `S06-unsharded` (standard topo) | 280 ms | 95 ms | 2.95× |
| `S06-sharded-2` (this topology) | **5.5 s** | (Oracle unchanged: 95 ms) | **58×** |
| `S06-sharded-2-cold` | **14 s** | (Oracle ~180 ms cold) | **78×** |

| Prediction | Confidence |
|------------|------------|
| Sharded variant ≥ 10× slower than unsharded variant on Mongo | Very high |
| Sharded variant explain shows classic-engine `$lookup` (no `EQ_LOOKUP`) | Very high |
| Mongo per-local-doc latency × matched local docs dominates total time | High |
| Oracle latency unchanged across topology variants | Very high |
| Sharded p99 / median ratio ≥ 4 (long tail on cross-shard fetches) | Medium-high |

Note: predictions are smaller in absolute time than at 10 M-order scale, but the *ratio* is what matters and is preserved.

## Pass/fail criteria

- **Strong pass:** Sharded-variant ratio ≥ 20× over Oracle baseline AND Mongo explain confirms classic-engine `$lookup` AND per-local-doc remote cursor pattern visible in mongos slow logs.
- **Pass:** Sharded ratio ≥ 10×, classic engine confirmed.
- **Fail:** Sharded ratio < 5× — investigate. Possible causes: shard-local matches dodging scatter-gather (verify `customer_id` hashing is actually distributing chunks); harness incorrectly connecting directly to a shard instead of mongos.

## Failure modes

- **Topology setup is fragile.** If config server fails to elect, mongos won't start. The harness retries `addShard`/`enableSharding` with backoff. If still failing after 3 attempts, S06 is skipped (with a warning), and the standard topology is brought back up so other scenarios can run.
- **Chunk migration during run.** New shards default to background balancer activity. The harness explicitly disables the balancer for the duration of the run: `sh.stopBalancer()` after `addShard`, re-enable on teardown. Otherwise mid-run chunk moves cause spurious latency spikes.
- **Cross-shard transactions are not used here** — `$lookup` doesn't require a transaction. Cross-shard transactions on a sharded customers would multiply the cost further; that's a separate scenario for a future revision.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `topology` | unsharded (standard), 2-shard | Primary comparison |
| `match_selectivity` | 1%, 10%, 50% of orders | Local-doc count drives per-doc remote cursor count |
| `local_doc_count` (computed) | proportional to selectivity | Exposes the linear cost-vs-doc-count |
| `cold_cache` | yes/no | Cold cache amplifies the per-doc fetch cost on the foreign side |
| Reverse-direction variant | `from: "orders"` (sharded), local: customers | Tests whether the sharded-foreign rule is symmetric. Requires re-sharding orders on `order_id` hashed. |

## Bringing the topology up and down

Pseudo-code for the harness's S06 lifecycle:

```python
def run_s06():
    # tear down standard topology
    docker.stop("mongo-bench")

    # bring up sharded topology
    docker.run("mongo-bench-shard1-router", image="custom-mongo-shard-router")
    docker.run("mongo-bench-shard2",        image="mongo-shardsvr")
    wait_for_mongos_ready(timeout=60)
    init_replica_sets()
    add_shards()
    sh.stopBalancer()
    enable_sharding("bench")
    shard_collection("bench.customers", { "customer_id": "hashed" })

    # load SF1 data
    mongorestore_through_mongos()

    # warmup + measure
    for variant in ["unsharded-baseline", "sharded-2"]:
        run_scenario_variant(variant)

    # tear down sharded topology
    docker.stop("mongo-bench-shard1-router")
    docker.stop("mongo-bench-shard2")

    # bring standard topology back
    docker.start("mongo-bench")
```

The unsharded-baseline variant inside this lifecycle is run via the *same mongos* — i.e., we connect through mongos but the `customers` collection is left unsharded. This isolates "sharded vs not" from "mongos vs direct connection," which is its own confounder.

## Build artifacts (Docker)

The harness's `infra/` directory will need:

- **`Dockerfile.mongo-shard-router`** — based on `mongodb/mongodb-community-server:8.2.2-ubuntu2404`, adds `supervisord` (or `s6-overlay`), an entrypoint script that starts configsvr → shardsvr → mongos in order with health checks.
- **`Dockerfile.mongo-shardsvr`** — based on the same upstream image, single-mongod entrypoint configured for `--shardsvr --replSet`.

Both images derive from the same upstream tag (`8.2.2-ubuntu2404`) so the engine binary is byte-identical across the standard and sharded topologies. Only the process orchestration differs.

The Dockerfile sources will live in `harness/infra/` once implementation begins; for the spec, the requirement is that they exist and are reproducible from the same upstream image tag pinned in `02-infrastructure.md`.
