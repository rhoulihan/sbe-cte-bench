# 02 — Infrastructure

## Why BYOE (bring your own environment)

This benchmark requires a **real Oracle environment with Exadata-class features** —
specifically Smart Scan, transparent storage offload, and the In-Memory column
store. Those are not in Oracle Database Free or `gvenzl/oracle-free`. Without them
the comparison would understate Oracle by measuring the *engine* in isolation
from the *infrastructure that engine is designed to run on*. Most production
Oracle deployments are on Exadata; benchmarking against Free is half the stack.

The cheapest way to get an Oracle environment in a benchmark is **Oracle
Cloud's Autonomous Database — Always Free tier.** It provides:

- 1 OCPU (= 2 ECPU equivalent ≈ 2 vCPU)
- ~3 GB shared SGA on Exadata-backed shared infrastructure
- 20 GB storage with HCC compression
- Production-grade HA with **99.95% SLA**, automatic backups, automatic patching
- 5 connection-tier services: `_high`, `_medium`, `_low`, `_tp`, `_tpurgent`
- $0/month, no time limit, no card required, **permanent**

⚠️ **One thing Always Free does NOT include is Smart Scan offload** — verified
empirically (plans show `TABLE ACCESS FULL`, not `TABLE ACCESS STORAGE FULL`,
even with `OPT_PARAM('cell_offload_processing','true')`). Smart Scan is gated
to paid ADB tiers. This means the bench measures Oracle **without** its
biggest performance feature — numbers shown are conservative.

## What we did to MongoDB to keep it competitive

There is no MongoDB equivalent of "Always Free Autonomous Database". MongoDB
Atlas M0 is explicitly tagged in the Atlas docs as **"not for production use"**
— it's a sandbox tier, single-node, no HA, no production SLA. To get a
MongoDB instance with anything close to production semantics we have to use
either Atlas M30+ (~$390/month dedicated cluster) or self-hosted on a paid VM.

We chose self-hosted on OCI compute, in the same region (us-ashburn-1) and
the same availability domain as the ADB instance, on the most generous
hardware that wasn't a punchline:

| | MongoDB host | ADB Always Free |
|---|---|---|
| **CPU** | **2 OCPU = 4 ECPU** (`VM.Standard.E5.Flex`) | 1 OCPU = 2 ECPU |
| **Memory** | **24 GB** | ~3 GB shared SGA |
| **Storage** | **Ultra-High-Performance paravirtualized block volume**, 120 VPU/GB → **115,000 IOPS / 920 MB/s** | shared Exadata storage |
| **Network to client** | **localhost** (client harness on same VM) | LAN within AD |
| **Region/AD locality** | same AD as ADB → no cross-AD network penalty | n/a |
| **Cost** | **paid: roughly $90–120/month for VM + storage** | **$0/month** |

The MongoDB host has **2× more compute, 8× more memory, dedicated high-throughput
local storage**, and zero network overhead to the client. The MongoDB process
itself is then **`systemd` cgroup-capped to 2 vCPU / 3 GB / 1.5 GB WT cache**
so the *workload-relevant compute envelope* roughly matches ADB's tier
allocation — but the OS, kernel, page cache, network buffers, and the harness
process all benefit from the full VM resources.

This is **the most charitable hardware setup we could give MongoDB short of
giving it more compute than ADB.** Larger Atlas tiers would cost hundreds to
thousands per month and explicitly throttle by cluster tier in ways that
mask engine architecture (M10–M40 IOPS scales with provisioned storage, not
with workload). The setup we chose **maximizes Mongo's chances** while
keeping the comparison defensible.

### The bench is memory-unbound

Headline storage measured during the actual sweep:

| | MongoDB (WT snappy) | Oracle ADB (OSON, no HCC) |
|---|---:|---:|
| All data (compressed) | ~480 MB | ~1.6 GB |
| WT cache cap / shared SGA | 1.5 GB | ~3 GB |
| Cache currently holding | 1,205 MB (steady state) | most of it |

The total working set is **smaller than each engine's cache budget**. Mongo's 1.5 GB WT cache holds the full 480 MB data + 106 MB indexes with ~295 MB to spare; ADB's ~3 GB shared SGA comfortably holds the 1.6 GB of Oracle segments. Both engines are cache-comfortable; neither is paying steady-state disk-read penalties.

**This is on purpose.** The bench is deliberately not a "who runs out of cache first" comparison. The architectural cliffs the bench surfaces — the 100 MB per-operator `$push` cap, the 16 MiB per-output-document BSON cap, classic-engine fallback paths, stage-bound pipeline semantics — are **hardcoded engine limits that don't scale with RAM**. Doubling the VM memory would not change any headline number. The result is "engine architecture beats hardware throwing", and we make that argument cleanly only by ensuring nobody can dismiss the bench as memory-starved.

### The asymmetry worth noting

We are benchmarking **a free service from Oracle that ships with HA, durability
guarantees, automatic backups, and a 99.95% production SLA** — running at a
1 OCPU envelope **without Smart Scan or In-Memory column store** — against
**MongoDB on infrastructure that costs hundreds of dollars per month to
operate**, with 2× the CPU headroom and 8× the memory headroom, on storage
provisioned for 920 MB/s sustained throughput, with localhost-routed clients.

And ADB still wins by 2–18×. This is the architectural argument the bench
is designed to surface: Oracle's engine architecture, even on a free shared
tier, beats MongoDB on dedicated paid compute that would be a five-figure
annual line item if you actually ran a fleet of these. There is no
configuration of MongoDB Atlas at any price tier that closes this gap on
the workloads measured here — the engine is doing the same fundamental
things it does on M0.

## Required components

| | Specification |
|---|---|
| **Oracle Autonomous DB** | Always Free tier (or larger). 1 OCPU. User-provided. Wallet downloaded to the client host. |
| **MongoDB** | 8.0+ community edition, native install (not Atlas), single-node replica set, journaling on, `internalQueryFrameworkControl=trySbeEngine`. Cgroup-capped to 2 vCPU / 3 GB / 1.5 GB WT cache so the *workload-relevant compute envelope* matches ADB's 1 OCPU tier. |
| **Client / Mongo host** | OCI Compute `VM.Standard.E5.Flex` with **2 OCPU / 24 GB**, in the same region and Availability Domain as the ADB instance. The OS, kernel, harness, and Mongo's network/page-cache layers benefit from the full VM resources; only mongod's CPU+memory are constrained to ADB's envelope. |
| **MongoDB storage** | **Ultra-High-Performance paravirtualized block volume, 120 VPU/GB**, mounted at `/mongo`. Sustained ~115,000 IOPS, 920 MB/s. Eliminates "Mongo is slow because IO" as a confound. |
| **Network** | Client harness on the same VM as Mongo (`localhost`). ADB reached via OCI internal network — same AD, sub-millisecond LAN latency. |

## Provisioning the environment (OCI Always Free)

### Step 1 — Provision the Autonomous Database

1. Sign up for Oracle Cloud (Always Free is permanent, no card required).
2. From the OCI console, navigate to **Oracle Database → Autonomous Database →
   Create Autonomous Database**.
3. Choose:
   - Display name: `rhbench` (or any label)
   - Database name: `RHBENCH`
   - Workload type: **Transaction Processing** (mix of OLTP + analytics)
   - Deployment type: Shared Infrastructure
   - **Always Free**: ON
   - Database version: 23ai or later (we tested 26ai EE 23.26.2.1.0)
   - ADMIN password: set a strong one (12-30 chars, must contain upper/lower/digit/special, no double quotes, no `&`, no spaces).
4. After provisioning (~3 min), click into the database. Click **Database
   Connection**, then **Download Wallet**. Set a wallet password (commonly
   different from the ADMIN password). Save the zip.

### Step 2 — Provision the client / MongoDB VM

This is **paid infrastructure** — the bench needs a Mongo host that isn't
artificially throttled, and Atlas Free is sandbox-only. We provision the
most generous VM shape that still keeps the comparison defensible:

1. From the OCI console, navigate to **Compute → Instances → Create instance**.
2. Choose:
   - Image: **Oracle Linux 9** (works with the install script in this repo)
   - Shape: **`VM.Standard.E5.Flex`**
   - **2 OCPU / 24 GB** — gives Mongo's host OS and harness 2× the compute
     and 8× the memory of ADB; mongod itself is cgroup-capped to ADB's
     envelope (see Step 3).
   - **Same Availability Domain as the ADB instance** — eliminates network
     penalty between client and ADB.
   - Networking: assign a public IP if you want SSH from outside OCI.
   - SSH keys: upload your public key.
3. **Attach an Ultra-High-Performance block volume**:
   - Compute → Block Storage → Create Block Volume
   - Size: 60 GB minimum (SF1 dataset + WT replication overhead)
   - Performance: **Ultra-High Performance, 120 VPU/GB** → ~115K IOPS, 920 MB/s
   - Attach to the VM with paravirtualized attachment.
4. Wait for the instance to be Running, then SSH in as `opc`.

### Step 3 — Install MongoDB on the client VM

```bash
curl -fLsSO https://raw.githubusercontent.com/rhoulihan/sbe-cte-bench/master/infra/install-mongodb-cgroup-capped.sh
bash install-mongodb-cgroup-capped.sh
```

This installs MongoDB 8.0, configures `mongod.conf` for single-node replica
set + journaling + SBE, applies systemd cgroup caps (`CPUQuota=200%`,
`MemoryMax=3G`, WT cache 1.5 GB) so that mongod's compute envelope matches
ADB Always Free's 1 OCPU tier, initializes `rs0`, and verifies the primary
is up. Takes ~2 minutes.

Then move Mongo's storage onto the high-perf block volume:

```bash
# Format the attached block volume as XFS with the right SELinux context
sudo mkfs.xfs -f -L mongo-data /dev/oracleoci/oraclevdc
echo '/dev/oracleoci/oraclevdc /mongo xfs defaults,_netdev,nofail,noatime 0 2' | sudo tee -a /etc/fstab
sudo mkdir -p /mongo && sudo mount /mongo

sudo systemctl stop mongod
sudo mkdir -p /mongo/data && sudo chown mongod:mongod /mongo/data && sudo chmod 750 /mongo/data
sudo dnf -q install -y policycoreutils-python-utils
sudo semanage fcontext -a -t mongod_var_lib_t '/mongo/data(/.*)?'
sudo restorecon -R /mongo

sudo sed -i 's|dbPath:.*|dbPath: /mongo/data|' /etc/mongod.conf
sudo systemctl start mongod
mongosh --quiet --eval 'rs.initiate({_id:"rs0", members:[{_id:0, host:"127.0.0.1:27017"}]})'
```

Mongo now runs on a 920 MB/s storage backend at ADB's compute envelope.

### Step 4 — Stage the wallet on the VM

From your local machine:

```bash
scp Wallet_rhbench.zip opc@<VM_IP>:/tmp/Wallet_rhbench.zip
```

On the VM:

```bash
mkdir -p ~/wallet
unzip /tmp/Wallet_rhbench.zip -d ~/wallet
chmod 700 ~/wallet && chmod 600 ~/wallet/*

# Fix sqlnet.ora — the wallet's default points to ?/network/admin
sed -i "s|?/network/admin|$HOME/wallet|" ~/wallet/sqlnet.ora
```

### Step 5 — Create the BENCH user in ADB

The harness operates as a non-DBA user (`BENCH`). Create it as `ADMIN`:

```sql
-- Connect as ADMIN/<your_admin_password> via SQL Developer Web or sqlplus
CREATE USER BENCH IDENTIFIED BY "Sbe2Cte_v1_Run_2026!"
  QUOTA UNLIMITED ON DATA;
GRANT CONNECT, RESOURCE, DWROLE TO BENCH;
ALTER USER BENCH DEFAULT TABLESPACE DATA;
GRANT UNLIMITED TABLESPACE TO BENCH;
```

ADB enforces password complexity (12-30 chars, mixed case, digit, special, no
double quotes, must not contain the username). The BENCH password used here
satisfies these.

### Step 6 — Set up the harness

```bash
git clone https://github.com/rhoulihan/sbe-cte-bench.git
cd sbe-cte-bench

# uv manages Python 3.12 and dependencies
curl -fLsS https://astral.sh/uv/install.sh | bash
export PATH=~/.local/bin:$PATH
uv sync --python 3.12

# Connect creds (export these or pass per-command)
export ORACLE_CONFIG_DIR=$HOME/wallet
export ORACLE_USER=BENCH
export ORACLE_PASSWORD='Sbe2Cte_v1_Run_2026!'
export ORACLE_DSN=rhbench_high           # or _medium / _low / _tp depending on need
export ORACLE_WALLET_PASSWORD='<wallet_password>'

# Verify both engines respond
uv run sbe-cte-bench infra verify
```

Expected output:

```
mongo preflight: MongoPreflightStatus(framework_control='trySbeEngine',
  journal_enabled=True, replica_set_initialized=True, server_version='8.0.21')
oracle preflight: OraclePreflightStatus(server_version='Oracle AI Database
  26ai Enterprise Edition Release 23.26.2.1.0 - Production', sga_target_mb=0,
  pga_aggregate_target_mb=0, statspack_installed=False, is_autonomous=True)
```

`is_autonomous=True` indicates the harness detected ADB and skipped the
DBA-only checks (`v$instance`, `v$parameter`, `dba_users` are restricted to
ADMIN on ADB).

### Step 7 — Generate + load data

```bash
uv run sbe-cte-bench data generate --scale SF1 --output-dir data/generated
uv run sbe-cte-bench data load --target both --data-dir data/generated
```

SF1 = 100K customers, 1M orders, 100K employees (org tree), 50K parts. Data
generation takes ~50-60 min on the Always Free 1 OCPU VM (single-threaded
Python); load is ~2 min for Mongo + ~3 min for ADB over the OCI internal
network.

## Service tier choice (`ORACLE_DSN`)

Autonomous DB exposes five connection services with different
parallel/concurrency profiles:

| Service | Parallel query | Concurrency | Use for |
|---|---|---:|---|
| `_high` | yes | 3 | Default for this bench. Analytical aggregations get parallel-execution help; CTE recursion can fuse iterations. |
| `_medium` | yes | 10 | Mixed workload baseline. |
| `_low` | no | 300 | Sequential serial execution; good for verifying we're not just measuring parallel-coordinator overhead. |
| `_tp` | no | 300 | OLTP. |
| `_tpurgent` | no | 300 | Highest priority OLTP. |

We default to `_high` because the article's claim is that Oracle's CBO **with
parallel-execution support** beats Mongo's classic-engine fallback paths.
Throttling Oracle to `_low` would artificially neuter the architectural
advantage. Set `ORACLE_DSN=rhbench_low` if you want a more conservative
comparison.

## Network considerations

Provision the client VM in **the same OCI region** as the ADB — both Always
Free tiers are in the home region you select at sign-up. Within-region OCI
network gives sub-millisecond LAN latency to ADB. Cross-region adds ~50-200 ms
per round-trip and dominates measurement.

The harness alternates Mongo and Oracle iterations per cycle. Even at LAN
speeds, the cumulative round-trip for the Oracle warmup+iter pattern (7×
queries with explain capture) adds ~1-2 sec per scenario variant; this is
absorbed into the warmup measurements.

## Verifying Smart Scan / In-Memory are firing

After loading, `EXPLAIN PLAN FOR <SQL>` on a large aggregation (e.g., S04)
should show `TABLE ACCESS STORAGE FULL` operators. Storage-side filtering
shows up as `STORAGE` row-source predicates. If you see `TABLE ACCESS FULL`
without `STORAGE`, you're on a non-Exadata service tier — possible on some
older ADB SKUs. The default Always Free is Exadata-backed.

For In-Memory, query `V$IM_SEGMENTS` from the BENCH user (read access is
granted via DWROLE):

```sql
SELECT segment_name, populate_status, bytes_not_populated
FROM v$im_segments
WHERE owner = 'BENCH';
```

ADB auto-populates large tables into the IM column store opportunistically;
this happens lazily after first analytical query. If you want immediate
population for the bench, run `ALTER TABLE orders_doc INMEMORY;` on the
hot tables after load.

## What's NOT in the test environment

- **Sharded MongoDB topology** — would require running multiple `mongod`
  instances + `mongos` router. The `infra/install-mongodb-cgroup-capped.sh`
  script sets up a single-node replica set; sharded scenarios (S06, sharded
  variants of S07/S14) are skipped on this baseline. To exercise them,
  follow MongoDB's official sharded-cluster docs and re-run the harness.

- **Exadata Smart Scan offload knobs** — Oracle Smart Scan is automatic on
  ADB; it's not a per-query feature you toggle. The benchmark observes
  whether it fires through `dbms_xplan` output, but doesn't try to
  manipulate it.

- **Statspack** — replaced by ADB's built-in AWR (DBA-only on Always Free).
  The harness gracefully skips Statspack collection when `is_autonomous=True`.
