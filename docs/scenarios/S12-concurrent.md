# S12 — Concurrent load

## Hypothesis

Single-iteration timings tell only part of the story. Production workloads are concurrent. Each engine has different concurrency primitives:

- MongoDB: per-operation memory caps mean each concurrent worker gets its own 100 MB cap, summing across N workers. Spill-to-disk pressure compounds. WiredTiger cache is shared.
- Oracle: PGA is governed by `PGA_AGGREGATE_TARGET` (600 MB on Free) shared across sessions; the workarea size policy auto-adjusts per-session grants. SGA buffer cache (1.2 GB on Free) is shared. Under high concurrency on Free, PGA pressure manifests early — at N=8 concurrent sessions, per-session workarea grants are ~75 MB, comparable to MongoDB's per-stage 100 MB cap.

When N concurrent workers each run a representative scenario (e.g. S02 or S04), the architectural behavior under concurrency diverges. Tail latency (p99) is the metric that shows the divergence — medians often look similar; tails do not.

**Expected:** Median latency rises modestly with concurrency on both engines; p99 latency rises super-linearly on Mongo (especially when hitting per-stage caps multiply concurrently); Oracle p99 rises sub-linearly until PGA pressure manifests at high concurrency.

## Article claim mapping

- Research dimension: concurrent-pipeline interference / working-set thrash.

## Data dependencies

- Scale factor: SF1.

## Indexes

Inherits from the base scenario (S02 by default).

## Workload structure

Each worker executes the S02 pipeline (top-100 customers by 90-day revenue with profile join) in a tight loop for 60 seconds after a 10-second ramp-up. Workers run as Python `multiprocessing` processes, each holding its own connection.

### Worker count sweep
- N ∈ {1, 2, 4, 8} — bounded by the 2-CPU constraint per engine container. Beyond N=8, per-worker queue-wait time dominates per-worker engine time, and we're measuring the kernel's task scheduler more than the engines.
- For each N: 60s of measurement + 10s ramp.
- Records every iteration's wall-clock latency, request-submission timestamp, and per-worker iteration index.

A future revision running on Oracle EE / unlimited MongoDB on a larger host would extend this sweep to N ∈ {16, 32, 64}; for v1.0 with Oracle Free's 2-CPU cap, N=8 is the meaningful ceiling.

### Per-worker pseudocode (Python)

```python
def worker(engine, scenario, duration_s):
    conn = open_connection(engine)
    timings = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        t0 = time.perf_counter_ns()
        run_query(conn, scenario)
        t1 = time.perf_counter_ns()
        timings.append((time.monotonic(), t1 - t0))
    return timings
```

The harness aggregates per-worker timings into a single CDF and computes:

- Throughput: `total_iterations / duration_s` (qps).
- p50, p90, p95, p99, p99.9 latency.
- Per-worker fairness: stddev of per-worker iteration count (high stddev = some workers starved).

## Predictions

| N | Mongo qps | Mongo p99 ms | Oracle qps | Oracle p99 ms |
|---|-----------|--------------|-------------|---------------|
| 1 | 8 | 180 | 11 | 110 |
| 2 | 14 | 260 | 20 | 145 |
| 4 | 18 | 580 | 32 | 220 |
| 8 | 20 | 1800 | 38 | 480 |

(Throughput ceilings are lower than typical "production" benchmarks because both engines are CPU-capped at 2 cores. The interesting metric is the *shape* of p99 growth, not the absolute qps.)

| Prediction | Confidence |
|------------|------------|
| Mongo throughput plateaus at lower N than Oracle | High |
| Mongo p99 grows super-linearly with N | High |
| Oracle p99 grows sub-linearly with N at low concurrency, kicks up at PGA pressure (PGA = 600 MB / N at N=8 is ~75 MB/session — workarea-tight) | Medium-high |
| At N=8, Mongo p99 / median ratio > 4× | High |
| At N=8, Oracle p99 / median ratio < 3× | Medium |

## Pass/fail criteria

- **Strong pass:** Mongo p99-at-N=8 / p99-at-N=1 ratio ≥ 8×; Oracle ratio ≤ 5×.
- **Pass:** Mongo p99 grows faster than Oracle p99 across the N sweep.
- **Fail (host bound):** Both engines' throughput plateaus at N=2 — neither engine is the bottleneck, the 2-CPU container limit is. Expected at N=8 anyway; the interesting comparison is the *shape* of the p99 curve up to that point.

## Failure modes

- **Connection saturation.** Mongo default `maxIncomingConnections = 65536`; Oracle Free default `processes = 320` — at N=8 we have 8 connections, well under both. But the harness still sets `--ulimit nofile=64000:64000` on both containers (per `02-infrastructure.md`).
- **Background tasks.** Both engines run background threads (compaction, MGW, log writer, etc.) that contend for CPU. Pinning containers to cpus 0-1 / 2-3 keeps them isolated from the harness, but cross-container scheduling effects are inherent. Run S12 last in a session so no other harness work is happening concurrently.

## Variations / sweep parameters

| Parameter | Values | Purpose |
|-----------|--------|---------|
| `N` | 1, 2, 4, 8 | Primary |
| `base_scenario` | S02, S04, S09 | What the workers actually run; tests whether the concurrency curve depends on the underlying scenario |
| `duration_s` | 60 (default), 300 (long-run) | Long-run exposes plan-cache pollution / stat invalidation effects |
| `workload_mix` | 100% S02, 80/20 S02/S04, 60/30/10 S02/S04/S09 | Realistic mixed workload approximation |
