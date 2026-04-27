# 06 — Instrumentation

What gets captured during every run. The instrumentation surface is large because the architectural claims under test are often subtle — a 10% latency difference is noise; a 10% latency difference *plus* an SBE→classic boundary visible in explain *plus* spill metrics that show one engine paged 200 MB to disk while the other did not is a story.

## Engine-side: MongoDB

For every scenario, capture:

### Plan
```javascript
db.<coll>.aggregate(<pipeline>, { explain: { verbosity: "executionStats" } })
```

Persist the full output. Key fields the harness extracts:

- `stages[].queryPlanner.winningPlan.stage` — top-level execution stage (e.g., `IDHACK`, `EXPRESS_IXSCAN`, `IXSCAN`, `COLLSCAN`, `GROUP`, `SORT`, etc.). Critical: detects whether the query accidentally hit Express Path (`EXPRESS_IXSCAN`/`EXPRESS_CLUSTERED_IXSCAN`) and bypassed the pipeline architecture we want to measure.
- `stages[].executionStats.executionStages.stage` — actual executed stage tree.
- The presence of `stages[0].$cursor.queryPlanner` — confirms the SBE→classic boundary signature documented in the article. The harness records the **stage index** at which `$cursor` first appears.
- `stages[].executionStats.totalKeysExamined`, `totalDocsExamined`, `nReturned` — per-stage selectivity.
- `stages[].executionStats.executionTimeMillisEstimate` — per-stage timing (engine-side, distinct from wall-clock).
- `serverInfo.version` — MongoDB version.
- `command.cursor` — confirms cursor-based delivery (vs aggregate-then-batch).

### Spill metrics (8.1+)

```javascript
db.adminCommand({ getDiagnosticData: 1 })  // not used for runs, just sanity check
```

For per-query spill metrics, the harness uses `system.profile`:

```javascript
db.setProfilingLevel(2)  // before run
// ... run query ...
db.system.profile.find({ "ts": { $gte: <run-start> } })
```

Captures: `<stage>Spills`, `<stage>SpillFileSizeBytes`, `<stage>SpilledDataStorageSize`, `<stage>SpilledRecords` per blocking operator. These exist in 8.1 with standardized names; in 8.0 the names vary slightly (legacy `usedDisk`).

### Plan cache and working set

```javascript
db.<coll>.getPlanCache().list()
db.serverStatus({ wiredTiger: 1 }).wiredTiger.cache
```

Capture before and after each run. Key fields:

- WiredTiger cache: `bytes currently in the cache`, `bytes read into cache`, `bytes written from cache`, `eviction worker thread evicting pages`, `pages evicted`.
- Plan cache: number of entries for the benchmarked collection; `isActive`; `works` count (FPTP iteration cost).

### Query framework knob

Verify `internalQueryFrameworkControl` matches expectations:

```javascript
db.adminCommand({ getParameter: 1, internalQueryFrameworkControl: 1 })
```

If a scenario expects SBE and the parameter says `forceClassicEngine`, the run is invalid.

## Engine-side: Oracle

For every scenario, capture:

### Plan

```sql
EXPLAIN PLAN SET STATEMENT_ID = '<scenario>-<iter>' FOR <SQL>;

SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', '<scenario>-<iter>',
  'TYPED ROWS BYTES COST PARTITION PARALLEL PREDICATE PROJECTION ALIAS REMOTE'));
```

Persist the full text plan. Key elements the harness extracts:

- Plan hash (`PLAN_TABLE_OUTPUT` line containing `Plan hash value`).
- Per-step operation name (e.g., `HASH JOIN`, `NESTED LOOPS`, `SORT GROUP BY`, `WINDOW SORT`, `JSON_TABLE EVALUATION`, `INDEX RANGE SCAN`, `TABLE ACCESS BY INDEX ROWID`).
- Whether CTE blocks were inlined or materialized (`TEMP TABLE TRANSFORMATION` step indicates materialization).
- Cardinality estimates per step (`Rows` column). Big estimate-vs-actual deltas are noted.

### Actual execution stats

```sql
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(
  '<sql_id>', NULL, 'ALLSTATS LAST'));
```

Captures actual rowcounts, per-step memory used (`Used-Mem`), per-step IO (`Reads`, `Writes`), workarea-policy-driven memory grant (`Used-Tmp`). Critical for measuring whether a CTE materialized to temp or streamed in memory.

### Workarea / PGA

```sql
SELECT * FROM v$sql_workarea_active WHERE sql_id = '<sql_id>';

SELECT name, value FROM v$pgastat
 WHERE name IN (
   'aggregate PGA target parameter',
   'aggregate PGA auto target',
   'global memory bound',
   'over allocation count',
   'extra bytes read/written',
   'cache hit percentage');
```

Captures: which operators ran one-pass vs multi-pass, peak workarea size, PGA pressure indicators.

### Buffer cache and stats

```sql
SELECT * FROM v$mystat NATURAL JOIN v$statname
 WHERE name IN (
   'consistent gets',
   'db block gets',
   'physical reads',
   'physical reads direct',
   'physical writes direct',
   'sorts (memory)',
   'sorts (disk)',
   'workarea executions - optimal',
   'workarea executions - onepass',
   'workarea executions - multipass');
```

### Reset between runs

```sql
ALTER SYSTEM FLUSH SHARED_POOL;
ALTER SYSTEM FLUSH BUFFER_CACHE;  -- only for cold-cache runs
```

### Statspack snapshots (the AWR-equivalent on Free)

**Oracle Database Free does not include AWR or Active Session History** — both are part of the Diagnostic Pack, an Enterprise Edition cost option. The free equivalent is **Statspack**, which has shipped with Oracle since 8i and remains fully functional in 26ai.

The harness captures **two Statspack snapshots per scenario** — one immediately before the timing-iteration loop begins, one immediately after — and produces a Statspack diff report covering only the iteration window. The report complements (does not replace) the per-query `dbms_xplan` and `v$sql_workarea_active` capture documented above.

#### Installation (one-time, during Oracle container build)

```sql
-- Run as SYS, AS SYSDBA
CREATE TABLESPACE perfstat DATAFILE '/opt/oracle/oradata/FREE/FREEPDB1/perfstat.dbf'
  SIZE 256M AUTOEXTEND ON NEXT 64M MAXSIZE 1G;

ALTER SESSION SET CONTAINER = FREEPDB1;
@?/rdbms/admin/spcreate.sql
-- Prompts: PERFSTAT_PASSWORD = bench; DEFAULT_TABLESPACE = perfstat; TEMP_TABLESPACE = TEMP
```

The PERFSTAT user owns the snapshot tables and is granted privileges to read the relevant `v$` views. The 256 MB tablespace easily accommodates dozens of snapshots; older snapshots are purged between scenarios.

#### Per-scenario snapshot capture

```sql
-- Before the timing-iteration loop:
EXECUTE STATSPACK.SNAP(i_snap_level => 7);
-- Returns the SNAP_ID via v$session output; harness records it.

-- ... iterations run ...

-- After the timing-iteration loop:
EXECUTE STATSPACK.SNAP(i_snap_level => 7);
-- Returns the second SNAP_ID.

-- Generate the diff report between the two snapshots:
@?/rdbms/admin/spreport.sql
-- Prompts: BEGIN_SNAP, END_SNAP, REPORT_NAME — harness fills these from recorded SNAP_IDs.
```

Snapshot **level 7** captures: SQL statements, segment statistics, latch statistics, parent latch children, and full system statistics. Level 7 is the right tradeoff between detail and snapshot cost (each snapshot at level 7 takes ~200 ms; cheap relative to the iteration loop).

#### What the report contains

Statspack reports closely mirror AWR's structure, with reduced ASH-driven detail:

- **Load profile** — per-second/per-transaction redo, logical reads, physical reads, parses, executes, transactions.
- **Top wait events** — what the database was waiting on during the window. Critical for spill-vs-IO-vs-latch diagnosis.
- **SQL ordered by elapsed time / CPU / gets** — the top-N SQL by various dimensions. The benchmark's primary scenario SQL should dominate; if something else does (housekeeping, recursive SQL), the run is invalid.
- **Instance activity stats** — full `v$sysstat` deltas (consistent gets, physical reads, sorts, parses).
- **Buffer pool activity** — read/write counts, hit ratios, free buffer waits.
- **PGA aggregate stats** — workarea sizing, optimal/onepass/multipass execution counts. Confirms the per-query `v$sql_workarea_active` data.
- **Latch activity** — latch waits, sleep counts, gets. Surfaces contention under S12 (concurrent load).
- **Tablespace IO stats** — per-tablespace read/write counts; confirms whether spill went to TEMP or whether buffer cache absorbed the workload.

#### Why this matters for the benchmark

For per-query architectural claims (S01–S10), the per-query `dbms_xplan` already shows the plan and per-step costs. For *systemic* claims — concurrent load (S12), plan-cache pollution (S15), planner stability across scale (S13) — the time-windowed Statspack report is the right granularity. We capture both because they answer different questions:

- Per-query instrumentation answers: *What plan ran? How much memory did each operator consume?*
- Statspack snapshots answer: *During the entire iteration window, what was the engine waiting on? Where did time go that doesn't show up in any single query's stats?*

Without Statspack, claims about contention, latching, and aggregate IO behavior would be supportable only by inference. Statspack makes them directly observable.

#### Persistence and reproducibility

Each Statspack report is written to `results/raw/<scenario>-<variant>-<timestamp>-statspack.txt` alongside the per-iteration JSON record. The run record's `oracle.statspack_report_path` field references it. Reports are flat text and review-friendly; they're committed to `results/processed/` for any published scenario.

#### What we *don't* try to replicate from AWR

- **Active Session History (ASH).** Diagnostic Pack only. The closest free analogue is sampling `v$session` periodically; the harness does this at 1 Hz during S12 (concurrent) runs and writes per-session captures to the run record. It's not as rich as ASH but is sufficient for tail-latency causation.
- **AWR Compare Periods report.** Statspack supports this via `sprepins.sql` — useful for V13 (planner stability under cardinality drift) where we want to compare snapshot-pairs from SF0.1 vs SF1 windows.

## OS-side

The harness wraps each iteration with OS-level timing:

```python
import time, resource
t0 = time.perf_counter_ns()
ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
result = run_query(...)
ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
t1 = time.perf_counter_ns()
```

Reported per iteration:

- `wall_ns` — `t1 - t0`.
- `cpu_user_ns` — `(ru1.ru_utime - ru0.ru_utime) * 1e9` from the engine container.
- `cpu_sys_ns` — same for stime.
- `peak_rss_kb` — `ru1.ru_maxrss`.
- `voluntary_csw` / `involuntary_csw` — context switches.
- `inblock` / `oublock` — block I/O counts.

Container-side counters are pulled from `/sys/fs/cgroup/<container>/cpu.stat`, `memory.peak`, `io.stat` immediately after each iteration. These give per-engine isolation that `getrusage` cannot.

For `iostat` / `pidstat` continuous monitoring during long-running iterations, use `bcc-tools` `biolatency` and `bpftrace` scripts under `harness/observability/` (TBD).

## Equivalence and correctness

After timings are collected, the harness:

1. Runs each query once more (outside the timing loop) with **server-side hashing**:
   - MongoDB: `{ $group: { _id: null, h: { $sum: { $function: { body: "function(...) { return canonicalHash(...); }", lang: "js" }}}}}`. Cumbersome — most scenarios just stream the result back to the harness for client-side hashing.
   - Oracle: `SELECT STANDARD_HASH(JSON_OBJECT(*) RETURNING CLOB, 'SHA256') FROM (...)` over the result.
2. Hashes both result sets client-side using `SHA-256` over the canonicalized rows (rows sorted, fields sorted alphabetically, JSON canonical form).
3. Asserts the two hashes match. If they don't, the scenario is invalid until the queries are reconciled.

## Snapshot of what a single run produces

```yaml
scenario: S03
variant: { boundary_position: 4 }
mongo:
  version: "8.2.2"
  framework_control: "trySbeEngine"
  pipeline: [ ... ]
  explain:
    sbe_prefix_length: 3
    cursor_at_stage: 4
    stages: [ ... per-stage timing/selectivity ... ]
  spill:
    "$group": { spilled_records: 0, spill_bytes: 0 }
    "$sort":  { spilled_records: 0, spill_bytes: 0 }
  timings_ms: [ 142.1, 138.4, 144.6, ... 20 values ]
  median_ms: 141.2
  p95_ms: 152.3
  iqr_ms: 6.1
  cv: 0.04
  cpu_user_ms: 121.0
  peak_rss_mb: 387
  csw_voluntary: 14820
oracle:
  version: "26.0.0.0"  # Oracle 26ai Free
  sql: |
    WITH ...
  plan:
    plan_hash: 3947211042
    inlined_ctes: ["recent_orders", "top_clients", "with_region", "bucketed"]
    materialized_ctes: []
    operations: [ ... ]
  workarea:
    peak_mem_mb: 142
    onepass_executions: 0
    multipass_executions: 0
  timings_ms: [ 38.9, 39.1, 38.7, ... 20 values ]
  median_ms: 38.9
  p95_ms: 41.0
  iqr_ms: 0.8
  cv: 0.02
  cpu_user_ms: 36.5
  peak_rss_mb: 211
  csw_voluntary: 4210
equivalence:
  mongo_hash: "f47ac10b58cc4372a567..."
  oracle_hash: "f47ac10b58cc4372a567..."
  match: true
prediction:
  expected_ratio: 3.5  # Mongo ≥ 3.5× slower than Oracle at boundary_position=4
  observed_ratio: 3.63
  pass: true
```

The full run record is JSON; this is the YAML view used in scenario summaries. The schema is defined in `07-reporting.md`.
