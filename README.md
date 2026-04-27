# sbe-cte-bench

**A reproducible benchmark comparing MongoDB's aggregation pipeline (with the Slot-Based Executor) to Oracle's CBO-driven CTE plans on Oracle Autonomous Database — with matched compute caps so the comparison reflects engine architecture, not headroom.**

## Why this benchmark exists

There is no published head-to-head comparing MongoDB's aggregation pipeline to a SQL/JSON CTE plan on the same workload, on Oracle's *real production stack* (Exadata-class storage, Smart Scan, In-Memory column store), at fairly-matched compute caps.

Prior JSON benchmarks measured CRUD or single-operator latency. The "First Past the Post" paper measured MongoDB's optimizer in isolation. Oracle's own Free / `gvenzl/oracle-free` lacks Smart Scan and IM column store — benchmarking against those measures the engine without the infrastructure it's built around.

This bench fills that gap: **MongoDB native install (cgroup-capped to match ADB envelope) vs Oracle Autonomous DB on Exadata, both running on OCI in the same region.**

## What's tested

Thirteen scenarios across the article's testable claims:

| | Scenario | What it measures |
|---|---|---|
| **S01** | Baseline scan + filter + project | Calibration; both engines should be comparable |
| **S02** | SBE-prefix best case | Mongo's best case for the SBE-fused indexed plan |
| **S03** | Boundary tax sweep (k=0..8) | `$redact` boundary at varying positions; tests SBE→classic transition cost |
| **S04** | 100 MB stage memory cap (ws=25→250 MB) | Mongo's per-stage cap forces spill; Oracle has none |
| **S05** | 16 MiB BSON document cap | `$group` accumulator that exceeds Mongo's per-output cap |
| **S06** | `$lookup` against sharded foreign | Mongo falls back to scatter-gather (sharded topology required) |
| **S07** | Recursive traversal: `$graphLookup` vs `CONNECT BY` | Org tree + path materialization at SF1 scale |
| **S08** | Window functions after non-pushable stage | `$setWindowFields` after `$facet`/`$bucketAuto`/`$graphLookup` |
| **S09** | Predicate pushdown / join reordering | CBO's freedom to reorder vs Mongo's stage-bound semantics |
| **S10** | Top-N optimization | `$sort` + `$limit` + downstream stages |
| **S12** | Concurrent load (N=1, 2, 4, 8) | Tail latency under contention |
| **S13** | Planner stability under cardinality drift | CBO replan vs Mongo FPTP |
| **S14** | Write path `$merge` vs `MERGE INTO` | Persisting aggregation results back |
| **S15** | Plan-cache pollution under bursty workload | 100/1K/10K distinct query shapes |

Each scenario produces a JSON record with timings, equivalence hashes, explain plans, and prediction outcomes.

## Test environment requirements (BYOE)

This benchmark requires a **real Exadata-backed Oracle environment** — Smart Scan, transparent storage offload, and In-Memory column store are central to the comparison. Oracle Database Free / `gvenzl/oracle-free` lacks all three; benchmarking against Free measures the engine without the infrastructure it was designed for.

Cheapest legitimate setup is **Oracle Cloud Always Free**:
- 1 OCPU Autonomous Database (Always Free, permanent, no card required)
- 1× `VM.Standard.A1.Flex` Compute instance for the harness + native MongoDB

MongoDB is then capped via systemd cgroups to **2 vCPU / 3 GB** — matching ADB's 1 OCPU envelope. Same compute budget, only architecture differs.

Full setup walkthrough: [`docs/02-infrastructure.md`](docs/02-infrastructure.md).

## Quick start (after provisioning OCI)

```bash
# On the OCI VM
git clone https://github.com/rhoulihan/sbe-cte-bench.git
cd sbe-cte-bench

# Native MongoDB with matched cgroup caps
bash infra/install-mongodb-cgroup-capped.sh

# Stage the wallet (download from OCI console)
mkdir -p ~/wallet && unzip /path/to/Wallet_*.zip -d ~/wallet
chmod 700 ~/wallet && chmod 600 ~/wallet/*
sed -i "s|?/network/admin|$HOME/wallet|" ~/wallet/sqlnet.ora

# Set up Python via uv
curl -fLsS https://astral.sh/uv/install.sh | bash
export PATH=~/.local/bin:$PATH
uv sync --python 3.12

# Connect creds
export ORACLE_CONFIG_DIR=$HOME/wallet
export ORACLE_USER=BENCH
export ORACLE_PASSWORD='<your_BENCH_password>'
export ORACLE_DSN=rhbench_high
export ORACLE_WALLET_PASSWORD='<your_wallet_password>'

# Verify both engines respond
uv run sbe-cte-bench infra verify

# Generate + load (SF1 ≈ 50 min generation + 5 min load)
uv run sbe-cte-bench data generate --scale SF1 --output-dir data/generated
uv run sbe-cte-bench data load --target both --data-dir data/generated

# Run scenarios — examples
uv run sbe-cte-bench run S01 --warmup 2 --iterations 5
uv run sbe-cte-bench run S04 --variant ws=100MB --warmup 2 --iterations 5

# Generate consolidated report
uv run sbe-cte-bench report all --raw-dir results/raw \
  --output results/processed/REPORT.md --scale-factor SF1
```

## Headline results (SF1, ADB Always Free vs cgroup-capped Mongo)

- **17–18× Oracle wins** at recursive traversal (S07 org-d10/d15, path-d10) — `$graphLookup` is classic-only; `CONNECT BY` does the same logical work in ~5% of the time
- **6.9–7.2× Oracle wins** at memory-bound aggregation (S04, all working-set sizes 25–250 MB) — Mongo saturates at ~87 s on the 3 GB cgroup cap; ADB on Exadata storage handles the 1M-order × line-item unwind in ~12 s
- **2.0–2.2× Oracle wins** consistently across S02, S03 (all 8 boundary positions), S08, S09, S10, S13, S14
- **Calibration parity** at S01 (~0.88×) — both engines comparable on trivial scan workloads

Full results: [`results/processed/EXECUTIVE_SUMMARY.md`](results/processed/EXECUTIVE_SUMMARY.md) and the auto-generated `REPORT.md`.

## What "fair" means here

- **Matched compute caps.** Mongo cgroup-capped to 2 vCPU / 3 GB exactly matching ADB Always Free's 1 OCPU envelope. No "Oracle has more hardware" confound.
- **Identical data.** Single deterministic generator with a pinned RNG seed produces byte-stable JSONL; both engines load from the same files.
- **Index parity.** Each scenario declares its index manifest; Mongo's B-tree and Oracle's function-based index on the JSON path get the same logical access path.
- **Equivalence verification.** SHA-256 of canonicalized result rows on both sides. The bench fails loudly when the engines compute different things.
- **Best-effort knobs.** Mongo: `internalQueryFrameworkControl=trySbeEngine`. Oracle: BENCH user with `CONNECT, RESOURCE, DWROLE`; default optimizer.

Full charter — including what we deliberately don't control for and why: [`docs/08-fairness-charter.md`](docs/08-fairness-charter.md).

## Repository layout

```
sbe-cte-bench/
├── README.md                                   ← you are here
├── docs/
│   ├── 00-overview.md                          ← scope and testable claims
│   ├── 01-methodology.md                       ← how each run is measured
│   ├── 02-infrastructure.md                    ← BYOE setup walkthrough (OCI Always Free)
│   ├── 03-data-model.md                        ← canonical schema + scale factors
│   ├── 04-indexes.md                           ← per-scenario index manifests
│   ├── 05-scenarios-index.md                   ← all 13 scenarios at a glance
│   ├── 06-instrumentation.md                   ← explain/AWR/OS-counter capture
│   ├── 07-reporting.md                         ← report shapes
│   ├── 08-fairness-charter.md                  ← what we control for, and why
│   └── scenarios/Sxx-*.md                      ← one spec per scenario
├── infra/
│   ├── install-mongodb-cgroup-capped.sh        ← native Mongo install with matched caps
│   ├── oracle-bench.sql                        ← optional helper SQL (BENCH user setup)
│   └── oracle-statspack.sql                    ← (Oracle Free only; ADB uses built-in AWR)
├── src/sbe_cte_bench/                          ← Python harness package
├── tests/                                      ← unit tests for the harness
└── results/
    ├── raw/                                    ← per-run JSON records (in .gitignore at runtime)
    └── processed/                              ← aggregated reports
```

## Status

**Ready for use against ADB Always Free.** Tested at SF1 (1M orders, 100K customers, 100K employees) on `us-ashburn-1` with `rhbench_high`. Results in `results/processed/`.
