# 02 — Infrastructure

## Why BYOE (bring your own environment)

This benchmark requires a **real Oracle environment with Exadata-class features** —
specifically Smart Scan, transparent storage offload, and the In-Memory column
store. Those are not in Oracle Database Free or `gvenzl/oracle-free`. Without them
the comparison would understate Oracle by measuring the *engine* in isolation
from the *infrastructure that engine is designed to run on*. Most production
Oracle deployments are on Exadata; benchmarking against Free is half the stack.

The cheapest way to get Smart Scan + In-Memory in a benchmark is **Oracle
Cloud's Autonomous Database — Always Free tier.** It provides:

- 1 OCPU (= 2 vCPU equivalent)
- ~3 GB SGA / shared infrastructure
- 20 GB storage
- Smart Scan offload to Exadata storage cells (transparent)
- In-Memory column store availability (auto-provisioned for hot tables)
- 5 connection-tier services: `_high`, `_medium`, `_low`, `_tp`, `_tpurgent`
- $0/month, no time limit, no card required

For MongoDB, we use a **native install with systemd cgroup caps** matching the
ADB envelope: `CPUQuota=200%` (= 2 vCPU = 1 OCPU equivalent) and `MemoryMax=3G`.
This is the apples-to-apples comparison: both engines have the same compute
budget; the architectural differences (Exadata storage offload vs Mongo's
classic-engine fallback paths) are the only independent variables.

## Required components

| | Specification |
|---|---|
| **Oracle Autonomous DB** | Always Free tier (or larger). User-provided. Wallet downloaded to client host. |
| **MongoDB** | 8.0+ community edition, native install (not Atlas), single-node replica set, journaling on, `internalQueryFrameworkControl=trySbeEngine`. Capped at 2 vCPU / 3 GB via systemd cgroup overrides. |
| **Client host** | OCI Compute VM in the same region as the ADB. Always Free supports a 4 OCPU VM (`VM.Standard.A1.Flex`) which is more than enough. |
| **Network** | Client VM must reach `adb.<region>.oraclecloud.com:1522` over TLS — automatic on OCI internal network. |

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

### Step 2 — Provision the client VM

1. From the OCI console, navigate to **Compute → Instances → Create instance**.
2. Choose:
   - Image: **Oracle Linux 9** (works with the install script in this repo)
   - Shape: `VM.Standard.A1.Flex` — the Always Free tier gives you 4 OCPU /
     24 GB. The benchmark fits comfortably; Mongo is cgroup-capped to a
     fraction of that envelope.
   - Networking: assign a public IP if you want SSH from outside OCI.
   - SSH keys: upload your public key.
3. Wait for the instance to be Running, then SSH in as `opc`.

### Step 3 — Install MongoDB on the client VM

```bash
curl -fLsSO https://raw.githubusercontent.com/rhoulihan/sbe-cte-bench/master/infra/install-mongodb-cgroup-capped.sh
bash install-mongodb-cgroup-capped.sh
```

This installs MongoDB 8.0, configures `mongod.conf` for single-node replica
set + journaling + SBE, applies systemd cgroup caps (2 vCPU / 3 GB),
initializes `rs0`, and verifies the primary is up. Takes ~2 minutes.

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
