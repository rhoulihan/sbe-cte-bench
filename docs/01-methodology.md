# 01 — Methodology

## Run structure

Every scenario produces one *run record* per (system, knob-set, dataset-scale) tuple. A run record contains:

- 3 warmup iterations (results discarded; cache and plan-cache primed).
- 20 measurement iterations (results retained for distribution).
- 1 verification pass (output diffed against the other system's output; see "Result equivalence" below).
- 1 explain capture (after warmup, before measurement; the explain plan must not change between iterations).

20 iterations is enough to compute a stable median and p95 for sub-100ms scenarios and adequate for p99 on scenarios where each iteration takes >500 ms. For long-running scenarios (>30s per iteration), n=10 is acceptable; the scenario spec must declare the override.

## Reporting

For every run we report:

- `median_ms` — middle value of the 20 timings.
- `p95_ms` — 19th-of-20 (i.e., 95th percentile by linear interpolation).
- `p99_ms` — only meaningful at n≥100; reported but flagged as low-confidence at n=20.
- `min_ms`, `max_ms`.
- `iqr_ms` — interquartile range (p75 − p25). The right "spread" metric for non-Gaussian timing distributions.
- `n` — number of iterations.
- `cv` — coefficient of variation (`stddev / mean`). Flag any run where `cv > 0.10` for re-execution.

We do **not** report the mean. Means are misleading for query timings — a single GC pause or checkpoint inflates the mean while leaving the median stable.

## Warmup

The 3 warmup iterations exist to:

1. Compile the MongoDB plan cache entry for the pipeline shape.
2. Hard-parse the Oracle SQL into the shared cursor cache.
3. Pull the working set into the WiredTiger cache / Oracle buffer cache.
4. Trigger any first-iteration JIT or bytecode-compilation overhead.

Warmup output is *discarded* — never folded into the measurement set. Warmup timings are kept in the run record under `warmup_ms[]` for diagnostic purposes only.

If the gap between warmup-3 and measurement-1 is >2× warmup-3, the run is rejected and logged. This catches checkpoint storms, log rotations, and other infrastructure noise.

## Cold-cache vs warm-cache runs

For every scenario, two runs are produced:

- **`warm`** — caches primed by warmup. The default. Reflects steady-state production behavior.
- **`cold`** — buffer/page cache flushed before each iteration. Reflects first-touch latency. Use `echo 3 > /proc/sys/vm/drop_caches` on Linux; `db.adminCommand({planCacheClear:1})` and `ALTER SYSTEM FLUSH BUFFER_CACHE` on the engines.

Cold-cache runs are noisy. Run with n=10 (or higher) and report median and IQR; do not report p95 or p99.

## Result equivalence

Before a run's timings are accepted as valid, the result of the MongoDB pipeline and the Oracle CTE must be verified equivalent.

Equivalence rules:

1. Same number of result rows.
2. Same set of values per (sorted) row, projected to the same scalar/array shape.
3. Floating-point comparisons use a relative tolerance of `1e-9`. If a scenario produces `SUM(amount)` and the two engines disagree by more than 1 unit in the last place of a `DOUBLE`, the scenario is broken.

Equivalence is verified by hashing the canonicalized result set on both sides and comparing the hashes. The hashing function is documented in `harness/equivalence.py` (TBD); for the spec, the rule is "two sets of rows, sorted lexicographically, with each row's fields canonicalized to the same JSON Pointer order, then SHA-256'd."

If equivalence fails, the run is **invalid** and the scenario is sent back for query re-design. We never report timings on non-equivalent queries — it is the most common way benchmarks deceive readers.

## Iteration ordering

Iterations alternate **system, system, system…** within a scenario. So for a scenario with N=20 iterations the actual execution order is:

```
warmup-mongo-1, warmup-oracle-1
warmup-mongo-2, warmup-oracle-2
warmup-mongo-3, warmup-oracle-3
mongo-1, oracle-1
mongo-2, oracle-2
…
mongo-20, oracle-20
verify-equivalence
explain-capture-mongo
explain-capture-oracle
```

Alternating prevents systematic bias from background processes (anti-virus scans, cron jobs, log rotations) that would otherwise hit one system disproportionately if all of one system's iterations ran before the other's.

## Isolation between scenarios

Between scenarios:

1. `db.adminCommand({planCacheClear: 1})` on every MongoDB collection touched.
2. `ALTER SYSTEM FLUSH SHARED_POOL` on Oracle.
3. `echo 3 > /proc/sys/vm/drop_caches` to drop OS page cache.
4. Wait 30 seconds for any background flush to settle.

This prevents cumulative plan-cache or working-set state from earlier scenarios distorting the next one's warmup.

## Concurrent-load scenarios

Scenarios that test concurrent load (S12) use a different methodology:

- Spawn N concurrent worker processes (not threads) using Python `multiprocessing`.
- Each worker submits the scenario's query continuously for a fixed wall-clock duration (60s after a 10s ramp-up).
- Per-iteration timings are recorded with monotonic-clock timestamps so the harness can compute throughput (qps), per-percentile latency, and tail-latency curves.
- N is varied across {1, 4, 8, 16, 32} — enough to expose contention without exhausting the host.

Concurrent-load runs report throughput AND latency together. A scenario where MongoDB hits 200 qps at p99 = 5s is not "winning" against Oracle at 180 qps and p99 = 200 ms.

## Statistical significance

For "MongoDB is slower than Oracle by X" to be a defensible claim:

- Median difference must be greater than `2 × max(IQR_mongo, IQR_oracle)`. Otherwise it's noise.
- Difference must persist across at least 2 of 3 dataset scales (small, medium, large).
- Difference must persist on cold-cache as well as warm-cache (or be explicitly flagged as warm-cache-only).

For "MongoDB fails where Oracle succeeds":

- The MongoDB query must return a documented error (`BSONObjectTooLarge`, `OperationFailed: $facet exceeded memory limits`, etc.) on at least 18 of 20 iterations at the declared scale.
- The Oracle query must return a correct result on 20 of 20 iterations.
- The failure mode must be re-confirmed at one larger scale to verify it's not a borderline case.

## Tooling for time measurement

- Python `time.perf_counter_ns()` brackets each query submission.
- The timer starts immediately before `cursor.execute()` (Oracle) or `client.aggregate()` (Mongo).
- The timer stops after the **last row is fetched** — not when the cursor is opened. A query that streams 10M rows and stops timing at the first batch is measuring nothing useful.
- Result iteration uses the natural cursor of each driver (`cursor.fetchall()` for Oracle; iteration over the `aggregate()` cursor for Mongo). Buffer size is left at driver default — explicitly documented per scenario.

## What we deliberately do not do

- **No driver-side parsing benchmarks.** Driver overhead is a real cost but it is not the engine architecture under test.
- **No serialization-format benchmarks.** That's `BSON-JSON-bakeoff` / DocBench territory. We assume identical app-side serialization cost on both sides.
- **No artificial sleeps or rate limiters.** If the engine spills to disk, we measure that; we don't smooth it out.
- **No retries on failure.** If a query errors, the error is recorded and the iteration counts as `null` (not retried). A scenario where Oracle succeeds 20/20 and MongoDB succeeds 17/20 + errors 3/20 is reported exactly that way.
