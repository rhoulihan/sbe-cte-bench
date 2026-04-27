# 02 — Infrastructure

## Hardware (reference baseline for v1.0)

The benchmark is sized to run within **Oracle Database 26ai Free's hard limits** (12 GB user data per PDB, 2 GB SGA+PGA combined, 2 CPU threads). MongoDB is then deliberately constrained to the same resource budget so the comparison reflects engine architecture, not headroom.

| Component | Specification |
|-----------|---------------|
| CPU | ≥ 4 physical cores total. Two are dedicated to each engine container; remaining capacity hosts the harness and absorbs OS noise. Any modern x86_64 laptop or workstation suffices. |
| Memory | ≥ 16 GB total. Two engine containers at 4 GB each + harness at 2 GB + OS overhead. |
| Storage | ≥ 100 GB free on a local SSD/NVMe. SF1 dataset + indexes ≈ 10 GB per engine; spill space and run records add ~30 GB headroom. **No network-attached storage** — IO ceilings would mask engine architecture. |
| Filesystem | XFS or ext4 with `noatime,nodiratime`. |
| Network | Localhost only. Drivers run on the same host as both engines. No TCP/IP latency in the measurement loop. |

A **2024-era developer laptop** (8-core CPU, 16 GB RAM, 500 GB NVMe) runs this benchmark end-to-end. That's the target reproducibility envelope.

**Atlas tiers (M0–M80) are explicitly rejected** for v1.0. Atlas tiers below M30 throttle IOPS; M30+ couples IOPS to provisioned-storage size in ways that make engine architecture unobservable. For the same reason, Oracle Autonomous Database tiers are rejected — ECPU-based throttling is a different observable than engine architecture. Both engines run **self-hosted in Docker** with matched resource limits.

## OS

| Component | Specification |
|-----------|---------------|
| Distribution | Ubuntu 24.04 LTS Server |
| Kernel | 6.8 or newer |
| Init | systemd |
| Cgroups | v2 (default in 24.04) |
| Swap | Disabled (`swapoff -a`; comment out swap entry in `/etc/fstab`). |
| Transparent Huge Pages | Disabled (`echo never > /sys/kernel/mm/transparent_hugepage/enabled`). MongoDB and Oracle both perform better without THP. |
| `vm.swappiness` | 1 |
| `vm.dirty_ratio` | 15 |
| `vm.dirty_background_ratio` | 5 |
| Time sync | `chrony` running, system clock synced. |

Tuning verification commands are in `docs/06-instrumentation.md`.

## Topology

Two topologies exist:

1. **Standard** (S01–S05, S07–S13, S15): single-node replica-set MongoDB + Oracle 26ai Free + harness. Both engines on identical 2-CPU / 4-GB Docker resource limits.
2. **Sharded** (S06, S07-sharded variant, S14 V14-c): two-container sharded MongoDB (1 mongos + configsvr + shard1 in container A; shard2 in container B) + Oracle 26ai Free + harness. MongoDB containers each get the same 2-CPU / 4-GB limit, so the sharded MongoDB cluster has 2× the resources of the Oracle container — a deliberate fairness exception documented in `scenarios/S06-lookup-sharded.md` and `08-fairness-charter.md`. All `mongod` instances (cfgsvr, shard1, shard2) run as single-node replica sets with journaling enabled — same as the standard topology's mongod.

Only one topology is live at a time. The harness shuts down the standard topology before starting the sharded one for S06, then restores standard after S06 completes.

The standard topology is described below. The sharded topology is in `S06-lookup-sharded.md`.

### Standard topology

Both database engines run on the same physical host, in containers, with explicit CPU pinning and memory budgeting:

```
┌──────────────────────── host (≥4 vCPU, ≥16 GB RAM) ───────────────────────┐
│                                                                            │
│  ┌─ MongoDB container ──────┐    ┌─ Oracle 26ai Free container ─────────┐  │
│  │ --cpus="2.0"             │    │ --cpus="2.0"                         │  │
│  │ --memory="4g"            │    │ --memory="4g"                        │  │
│  │ --cpuset-cpus="0-1"      │    │ --cpuset-cpus="2-3"                  │  │
│  │ wt cache: 1.5 GB         │    │ SGA: 1.2 GB; PGA: 0.6 GB             │  │
│  │ disk: /data/mongo        │    │ disk: /data/oracle                   │  │
│  └──────────────────────────┘    └──────────────────────────────────────┘  │
│                                                                            │
│  ┌─ harness container (--cpus="1.0", --memory="2g") ─────────────────┐     │
│  │ Python 3.12, pymongo, python-oracledb, matplotlib                  │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│  ┌─ idle reserve (remaining host CPUs) ──────────────────────────────┐     │
│  │ absorbs OS noise, kthreads, container background work             │     │
│  └────────────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────────────┘
```

Both engine containers have **identical CPU and memory budgets** — 2 CPUs and 4 GB Docker memory, with engine-internal RAM (WT cache for Mongo, SGA+PGA for Oracle) sized symmetrically at ~1.8 GB. This is the maximum Oracle Free permits; MongoDB is constrained to match.

Why pinning matters: aggregation pipeline stages and CTE operators are CPU-intensive. Without pinning, the kernel scheduler can migrate threads across NUMA nodes mid-iteration, introducing 200–500 µs jitter that swamps small-scenario timings. On laptops, NUMA is usually irrelevant (single socket); on workstations or servers, both containers should be pinned to the same NUMA node and memory-bound (`numactl --membind=0`).

## Container resource limits

Both engine containers are launched with **identical Docker resource limits**, matched to Oracle Free's hard caps:

```bash
docker run -d \
  --name=mongo-bench \
  --cpus="2.0" \
  --memory="4g" \
  --memory-swap="4g" \
  --cpuset-cpus="0-1" \
  --memory-swappiness=0 \
  --ulimit nofile=64000:64000 \
  --volume /data/mongo:/data/db \
  -p 27017:27017 \
  mongodb/mongodb-community-server:8.2.2-ubuntu2404 \
  --replSet=bench \
  --bind_ip_all \
  --wiredTigerCacheSizeGB=1.5 \
  --journalCommitInterval=100

# After container start, initialize the single-node replica set:
docker exec mongo-bench mongosh --eval '
  rs.initiate({ _id: "bench", members: [{ _id: 0, host: "mongo-bench:27017" }] })
'

docker run -d \
  --name=oracle-bench \
  --cpus="2.0" \
  --memory="4g" \
  --memory-swap="4g" \
  --cpuset-cpus="2-3" \
  --memory-swappiness=0 \
  --ulimit nofile=64000:64000 \
  --shm-size=2g \
  --volume /data/oracle:/opt/oracle/oradata \
  -p 1521:1521 \
  -e ORACLE_PWD=BenchPass2026 \
  container-registry.oracle.com/database/free:26ai
```

Both containers have the same number of CPUs (`--cpus="2.0"`), the same memory ceiling (`--memory="4g"`), the same memory-swap policy, and the same file-descriptor ulimit. Disk volumes are sibling directories on the same physical device. The `--shm-size=2g` flag is Oracle-specific — Oracle uses POSIX shared memory for SGA and the default container `/dev/shm` (64 MB) is too small.

The `--cpus="2.0"` limit hits Oracle Free's 2-CPU-thread cap exactly. MongoDB has no equivalent cap; matching to Oracle's is a deliberate fairness constraint — see `08-fairness-charter.md`.

**Equivalence audit before each run:** the harness queries `docker stats --no-stream --format "{{.Container}} {{.CPUPerc}} {{.MemUsage}} {{.MemPerc}}"` and `docker inspect` for both containers to verify identical resource limits at iteration time. If a container has been rebuilt with different limits, the run is invalid.

## MongoDB build

Every `mongod` instance in this benchmark — across both the standard and sharded topologies — runs as a **single-node replica set with journaling enabled**. Specifically:

- Standard topology: one `mongod --replSet=bench` initialized via `rs.initiate({_id:"bench", members:[{_id:0, host:"mongo-bench:27017"}]})`.
- Sharded topology: three single-node replica sets total — `cfgRS` (configsvr), `shard1` (shardsvr), `shard2` (shardsvr) — each initialized identically.
- **Journaling**: WiredTiger journal is enabled by default in MongoDB 8.x and *cannot be disabled* (the `--nojournal` flag was removed in 6.1). The spec explicitly verifies via `db.serverStatus().wiredTiger.log` that the journal is active and that journal flush metrics are non-zero during write workloads.

Why single-node replica sets specifically:

1. **Transactions require a replica set.** A standalone `mongod` cannot run multi-document transactions; both `$out`/`$merge` inside transactions (S14) and the implicit transaction semantics of `$lookup` semantics on shard-versioned reads need a replica set.
2. **Replica sets enforce write durability semantics.** With `w:1` (default) and journaling on, every committed write is durable to disk before the client acknowledgment returns. This matches Oracle's `COMMIT` semantics and makes write-throughput measurements (S14) directly comparable.
3. **Single-node** keeps the footprint within Oracle Free's resource envelope. We're not benchmarking replication overhead; we're benchmarking aggregation architecture. A 3-node replica set would add network latency between nodes that has nothing to do with the question under test.

| Setting | Value | Notes |
|---------|-------|-------|
| Source | Official `mongodb/mongodb-community-server:8.2.2-ubuntu2404` image (or any 8.2.x patch tag) | Pin tag exactly. Do not use `latest`. |
| Engine | WiredTiger (default) | |
| `replication.replSetName` | `bench` (standard) / `cfgRS`, `shard1`, `shard2` (sharded) | All `mongod` instances run as single-node replica sets. |
| `storage.journal.enabled` | `true` | Default and unchangeable in 8.x. Verified in pre-run audit. |
| `storage.syncPeriodSecs` | `60` | Default WT checkpoint cadence. |
| `storage.wiredTiger.engineConfig.cacheSizeGB` | **1.5** | Sized to fit within the 4 GB Docker container budget after WT overhead, OS, and the mongod process. Symmetric to Oracle's SGA. |
| `setParameter.internalQueryFrameworkControl` | `trySbeEngine` | **Critical.** Forces SBE on for SBE-eligible queries. Default in 8.0 is `trySbeEngine`; we explicitly set it for paranoia and forward-compatibility. |
| `setParameter.allowDiskUseByDefault` | `true` | Default in 6.0+. Set explicitly. |
| `setParameter.internalDocumentSourceGroupMaxMemoryBytes` | `104857600` (100 MB) | The default. We pin it to defeat any host-OS variation. Scenarios may override for ablation. **Note:** 100 MB is ~7% of the WT cache; Oracle's PGA is ~30% of its SGA. Same architectural per-stage cap, very different fraction of total RAM. This is the architectural difference S04 exists to measure. |
| `setParameter.internalQueryMaxAddToSetBytes` | `104857600` | Default. |
| `setParameter.internalQueryMaxPushBytes` | `104857600` | Default. |
| `setParameter.internalQueryExecMaxBlockingSortBytes` | `104857600` | Default. |
| `net.compression.compressors` | (default) | Driver compression off for the harness. |
| `operationProfiling.mode` | `slowOp` | |
| `operationProfiling.slowOpThresholdMs` | `0` | Profile every operation during a benchmark run; turn off between runs. |

The MongoDB instance runs as a single-node replica set (`--replSet bench`) so it supports `$out`/`$merge` inside transactions where applicable. It is **not** sharded for the standard topology — sharded scenarios (S06 only) use a separate, dedicated **2-container sharded topology** (1 mongos + cfgsvr + shard1 in container A; shard2 in container B), described in detail in `scenarios/S06-lookup-sharded.md`. The harness swaps topologies (stops standard, starts sharded, runs S06, stops sharded, restarts standard) so only one topology is live at a time.

S14 (write path with sharded `$merge` target) reuses the S06 sharded topology when its V14-c variant runs.

## Oracle build

| Setting | Value | Notes |
|---------|-------|-------|
| Source | `container-registry.oracle.com/database/free:26ai` | Pin tag exactly. |
| Edition | Free | **Hard limits enforced by Free:** 12 GB user data per PDB, 2 GB SGA+PGA combined, 2 CPU threads, 1 PDB. The benchmark sizes itself within these caps. |
| `MEMORY_TARGET` | unset | Use explicit SGA + PGA targets instead — `MEMORY_TARGET` and AMM are deprecated. |
| `SGA_TARGET` | `1200M` | Within Free's 2 GB combined cap. |
| `PGA_AGGREGATE_TARGET` | `600M` | Within Free's 2 GB combined cap. Total = 1.8 GB engine RAM, leaving headroom inside the 4 GB container. |
| `WORKAREA_SIZE_POLICY` | `AUTO` | Default. |
| `OPTIMIZER_INDEX_COST_ADJ` | (default) | Don't tilt the optimizer. |
| `_optimizer_use_feedback` | `TRUE` | Default. Allows adaptive plan tuning between runs. Cleared between scenarios. |
| `RESULT_CACHE_MODE` | `MANUAL` | Disable automatic result caching to prevent hits across iterations. |
| `OPTIMIZER_CAPTURE_SQL_PLAN_BASELINES` | `FALSE` | No baseline pinning. We want the CBO to plan freshly per scenario. |
| Tablespace | `BENCH_DATA` on `/opt/oracle/oradata/FREE/FREEPDB1/bench_data.dbf` (autoextensible to 11 GB max) | Sized to fit within Free's 12 GB user-data cap with 1 GB headroom for system segments. |
| Temp tablespace | Default `TEMP` (managed by Free image) | Spill operations use this; not counted against the 12 GB user-data cap. |
| User | `BENCH` (created with `DBA` role for setup, downgraded to specific privileges for runs) | |
| Compatible | `26.0.0` | |
| **Statspack** | Installed | AWR is part of the Diagnostic Pack and not included in Oracle Free. Statspack — Oracle's free, included-since-8i performance repository — provides equivalent system-wide snapshot reports. Installed via `?/rdbms/admin/spcreate.sql`. Used for system-wide instrumentation per `06-instrumentation.md`. |
| `PERFSTAT` user | Created during init | Owns Statspack tables; password pinned per run for reproducibility. |
| `PERFSTAT` tablespace | 256 MB autoextending to 1 GB on `/opt/oracle/oradata/FREE/FREEPDB1/perfstat.dbf` | Sized for dozens of snapshots; purged between scenarios. |

A single PDB (`FREEPDB1` is fine) hosts everything. We do not use a multitenant CDB switch in the timing loop.

## Harness host

The harness runs in a dedicated container with `--cpus="1.0"` and `--memory="2g"`, pinned to a CPU outside the 0–3 range used by the engine containers. The harness is single-threaded (or single-process for concurrent-load scenarios). It connects via:

- MongoDB: `mongodb://localhost:27017/?replicaSet=bench&directConnection=true`
- Oracle: `oracledb.connect(user="BENCH", password="…", dsn="localhost/FREEPDB1")` in thin mode. (Oracle 26ai Free's default service is `FREEPDB1` per the 26ai container image.)

Both connections use the loopback interface. No TLS. No driver-side compression. These are bench-only choices that document the *engine* architecture and not the wire format.

## Reproducibility

The full setup is captured as a `compose.yaml` file in `harness/infra/` (TBD). Running `docker compose up` should bring up both containers, the harness container, and a `setup` job that creates the schemas, tablespaces, and benchmark user.

The container images, the OS image, and the harness Python version are all pinned. The benchmark must be runnable end-to-end from a clean host in under 30 minutes (data load excluded — see `docs/03-data-model.md` for scale-factor durations).
