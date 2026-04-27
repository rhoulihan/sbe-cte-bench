# Implementation Plan — sbe-cte-bench

This is the implementation plan for the benchmark spec in `docs/`. The spec is the *contract* (what to build); this plan is the *how* (in what order, with what tooling, validated by what tests, gated by what CI).

The plan is opinionated. Choices are justified inline. Where the spec leaves a decision open, this plan closes it.

---

## Table of contents

1. [Principles](#1-principles)
2. [Tooling stack](#2-tooling-stack)
3. [Project structure](#3-project-structure)
4. [Phase plan](#4-phase-plan)
5. [TDD workflow](#5-tdd-workflow)
6. [`pyproject.toml` reference](#6-pyprojecttoml-reference)
7. [Pre-commit hooks reference](#7-pre-commit-hooks-reference)
8. [CI/CD pipeline reference](#8-cicd-pipeline-reference)
9. [Test strategy](#9-test-strategy)
10. [Quality gates](#10-quality-gates)
11. [Risk register](#11-risk-register)
12. [Definition of done](#12-definition-of-done)

---

## 1. Principles

These are non-negotiable. Every decision below traces back to one.

1. **Spec is the contract.** Every implementation artifact (module, fixture, test, scenario runner) maps to a section of `docs/`. Drift is a bug; the spec wins.
2. **Test-first or it didn't happen.** No production code is committed without a failing test that motivates it. The PR template asks for the failing-then-passing commit pair as evidence.
3. **CI gates only on what CI can decide.** Wall-clock benchmark numbers come from dedicated hardware, not GitHub-hosted runners. CI verifies correctness, equivalence, schema conformance, deterministic data generation, plot-rendering reproducibility — not latency.
4. **Reproducibility is the product.** Anyone with Docker should clone, run one command, and reproduce a result identical to a published one. If a result can't be reproduced, it isn't a result.
5. **Fairness is structural, not ad-hoc.** The fairness charter (`docs/08-fairness-charter.md`) is enforced by code — pre-iteration audits compare resource limits between containers, scenarios that violate parity fail before timing.
6. **Defaults must be sound.** A user who runs `uv run sbe-cte-bench all` without flags gets a defensible result. Customization is opt-in.

---

## 2. Tooling stack

| Concern | Choice | Why |
|---------|--------|-----|
| Build backend | `hatchling` | Modern PEP 517 default; minimal config; no legacy baggage. |
| Layout | `src/` | Forces tests to import the *installed* package — same code path users get. PyPA recommended. |
| Dependency manager | `uv` (Astral) | ~10× faster than Poetry; universal lockfile (`uv.lock`) covers all platforms in one resolution; manages the Python toolchain itself. PEP 735 `[dependency-groups]` for dev/test/docs. |
| Python version | **3.12 only** | A benchmark *harness* must control its own runtime variation. Multi-version matrix is appropriate for libraries; not for instruments. |
| Linter | `ruff` | Replaces flake8 + isort + pyupgrade + flake8-bugbear + flake8-bandit etc. in one fast Rust binary. Production stable. |
| Formatter | `ruff format` | Black-compatible drop-in (>99.9% line parity); same binary as the linter. |
| Type checker | `mypy --strict` (CI gate) + Pyright (local, via Pylance) | Mypy strict is the deterministic gate; Pyright is faster for editor feedback. Don't pick one — use both for their strengths. |
| Pre-commit | `pre-commit` framework (pre-commit.com) | Still the standard. |
| Testing | `pytest` + `pytest-cov` + `pytest-xdist` + `hypothesis` | Universal language. xdist for parallelism; hypothesis for property-based equivalence checking. |
| Docker fixtures | `testcontainers-python` | Random ports avoid xdist collisions; built-in waiters; Pythonic per-fixture lifecycles. |
| Coverage | `coverage.py` via `pytest-cov` | Universal CI integration. SlipCover is interesting but smaller ecosystem. |
| Secret scanning | `gitleaks` | Single Go binary; 150+ rules; faster than detect-secrets for new repos without legacy baselines. |
| Oracle CI image | `gvenzl/oracle-free` (Docker Hub) | The official `container-registry.oracle.com/database/free` requires Oracle SSO login — awkward for public CI. `gvenzl` is the canonical community image: no auth, multi-arch, faststart variants. We pin by digest for reproducibility. The docs/02-infrastructure.md spec still calls out the official image as the *primary* target; gvenzl is the CI mirror. |
| MongoDB CI image | `mongodb/mongodb-community-server:8.2.2-ubuntu2404` | Official image; no auth required from Docker Hub. Pinned tag. |
| CI platform | GitHub Actions (with `astral-sh/setup-uv@v3`) | Native to where the repo lives; service containers + testcontainers cover all topology shapes. |
| Plot rendering | `matplotlib` + `seaborn` | Fixed `svg.hashsalt` and version-pinned for byte-stable SVG output. |
| Doc tooling | None for v1.0 | Markdown is the format. No Sphinx, no MkDocs, no static site. The `docs/` directory is the deliverable. |

### Tooling not used and why

- **Poetry** — slower than uv on resolution; lockfile is platform-locked; doesn't manage Python versions. Acceptable but uv is strictly better in 2026.
- **pip-tools** — works fine but uv superset its functionality and adds a lot more.
- **black** — `ruff format` is now a drop-in replacement and lives in the same binary as the linter.
- **flake8** + plugins — entirely subsumed by ruff.
- **bandit** — Ruff's `S` ruleset (flake8-bandit) covers it; standalone bandit is reserved for security-critical applications.
- **pyre / pytype** — neither is being actively recommended for new projects; mypy and pyright dominate.
- **tox / nox** — uv runs scripts directly via `uv run`; no need for orchestrator on top.
- **Mutation testing** (mutmut, cosmic-ray) — the equivalence checker is itself a metamorphic differential test against two independently implemented engines. Mutation testing of unit assertions is redundant and expensive here. Reconsider only if the harness becomes a library others depend on for correctness claims.
- **Sphinx / MkDocs** — the spec is markdown in `docs/`. Adding a doc site is yak-shaving for v1.0.

---

## 3. Project structure

```
sbe-cte-bench/
├── README.md
├── IMPLEMENTATION-PLAN.md           ← this file
├── pyproject.toml                   ← single source of project config
├── uv.lock                          ← committed; reproducible install
├── .pre-commit-config.yaml
├── .gitignore
├── .gitleaks.toml                   ← secret-scanning config
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                   ← lint, type, unit, integration
│   │   ├── nightly.yml              ← full integration on self-hosted (or scheduled cloud)
│   │   └── release.yml              ← tag → reproducibility manifest validation
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── scenario_proposal.md
├── docs/                            ← the benchmark spec (immutable contract)
│   └── … (see 05-scenarios-index.md)
├── src/
│   └── sbe_cte_bench/               ← the actual package
│       ├── __init__.py
│       ├── __main__.py              ← `uv run sbe-cte-bench …` entrypoint
│       ├── cli.py                   ← Click / Typer-driven command surface
│       ├── config/
│       │   ├── __init__.py
│       │   ├── manifest.py          ← run-record schema, parsed from spec
│       │   ├── scenarios.py         ← ScenarioSpec: title, predictions, queries
│       │   └── topology.py          ← Standard / Sharded enum, container limits
│       ├── infra/
│       │   ├── __init__.py
│       │   ├── compose.py           ← bring up / tear down topology
│       │   ├── verify_limits.py     ← pre-iteration resource-limit audit
│       │   └── topology_swap.py     ← S06/S07-sharded/S14c lifecycle
│       ├── data/
│       │   ├── __init__.py
│       │   ├── schema.py            ← Pydantic models for all entities
│       │   ├── generator.py         ← deterministic data emitter
│       │   ├── extensions.py        ← S04 deep-skew, S05 hot-customer
│       │   ├── manifest.py          ← post-load hashes
│       │   ├── load_mongo.py       ← mongorestore wrapper
│       │   └── load_oracle.py       ← SQL*Loader / direct insert wrapper
│       ├── drivers/
│       │   ├── __init__.py
│       │   ├── mongo.py             ← thin pymongo wrapper
│       │   └── oracle.py            ← thin python-oracledb wrapper (thin mode)
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── mongo_explain.py     ← parses executionStats, finds SBE/classic boundary
│       │   ├── oracle_xplan.py      ← dbms_xplan capture & parse
│       │   ├── oracle_statspack.py  ← STATSPACK.SNAP + spreport.sql automation
│       │   ├── spill_metrics.py     ← 8.1+ Mongo per-stage spill counters
│       │   └── os_counters.py       ← /proc, getrusage, cgroup v2 reads
│       ├── equivalence/
│       │   ├── __init__.py
│       │   ├── canonicalize.py      ← row → canonical form
│       │   ├── hash.py              ← SHA-256 over canonicalized result set
│       │   └── verify.py            ← equivalence check pipeline
│       ├── runner/
│       │   ├── __init__.py
│       │   ├── timing.py            ← perf_counter_ns brackets, percentile math
│       │   ├── warmup.py            ← warmup logic, cold-cache management
│       │   ├── alternating.py       ← system, system, system iteration order
│       │   ├── concurrent.py        ← S12 multiprocess driver
│       │   └── scenario_runner.py   ← top-level scenario execution
│       ├── scenarios/               ← one module per Sxx, matching docs/scenarios/
│       │   ├── __init__.py
│       │   ├── _base.py             ← ScenarioBase abstract class
│       │   ├── s01_baseline.py
│       │   ├── s02_sbe_prefix.py
│       │   ├── s03_boundary_tax.py
│       │   ├── s04_stage_wall.py
│       │   ├── s05_doc_cap.py
│       │   ├── s06_lookup_sharded.py
│       │   ├── s07_graphlookup.py
│       │   ├── s08_window_functions.py
│       │   ├── s09_predicate_pushdown.py
│       │   ├── s10_top_n.py
│       │   ├── s11_oson_deep.py
│       │   ├── s12_concurrent.py
│       │   ├── s13_planner_stability.py
│       │   ├── s14_write_path.py
│       │   └── s15_plan_cache.py
│       └── reporting/
│           ├── __init__.py
│           ├── aggregate.py         ← results/raw → results/processed
│           ├── plot.py              ← matplotlib SVG charts (deterministic)
│           ├── claim_coverage.py    ← cross-scenario claim 11 summary
│           └── markdown.py          ← per-scenario writeup generator
├── tests/
│   ├── conftest.py                  ← top-level fixtures (testcontainers, etc.)
│   ├── unit/                        ← no DB, no Docker; pure-logic only
│   ├── integration/                 ← Docker required; testcontainers fixtures
│   ├── property/                    ← hypothesis-driven
│   ├── e2e/                         ← full scenario runs at SF0.001 (CI-sized)
│   ├── golden/                      ← byte-stable SVG, hash, JSON fixtures
│   └── fixtures/                    ← shared test data
├── infra/                           ← Docker artifacts (referenced by harness)
│   ├── compose.standard.yaml
│   ├── compose.sharded.yaml
│   ├── Dockerfile.mongo-shard-router
│   ├── Dockerfile.mongo-shardsvr
│   ├── mongo-bench.cnf
│   ├── oracle-bench.sql
│   ├── oracle-statspack.sql
│   └── topology-swap.sh
├── data/                            ← gitignored; generated output
└── results/                         ← gitignored except processed/
    ├── raw/
    ├── processed/
    └── charts/
```

The `harness/` directory in the spec is replaced by `src/sbe_cte_bench/`. The `harness/README.md` content is folded into this plan (the README stays as a pointer).

---

## 4. Phase plan

Eleven phases, ordered by dependency. Each phase has explicit acceptance criteria; the next phase cannot start until they're met. Estimates are for one experienced engineer working full-time.

### P0 — Project scaffolding (2 days)

**Goal:** A repo where `uv sync && uv run pytest && pre-commit run --all-files` succeeds on an empty test, with CI green.

**Deliverables:**
- `pyproject.toml` with dependencies, dev-group, ruff/mypy/pytest config
- `uv.lock` committed
- `.pre-commit-config.yaml` with all hooks wired
- `.github/workflows/ci.yml` — lint, type-check, smoke test
- `src/sbe_cte_bench/__init__.py` with `__version__ = "0.1.0.dev0"`
- One trivial passing test (`tests/unit/test_smoke.py`) and one trivial typed module
- Pre-commit hooks installed and verified to fire

**TDD start:** `tests/unit/test_smoke.py` asserts `sbe_cte_bench.__version__` is a non-empty string. Write it failing (no module), implement the module, watch it pass.

**Acceptance:**
- `uv run pytest` green
- `uv run mypy src/` green
- `uv run ruff check src/ tests/` green
- `uv run ruff format --check src/ tests/` green
- `pre-commit run --all-files` green
- CI workflow green on the first push

### P1 — Infrastructure + topology (3 days)

**Goal:** Standard topology comes up via `uv run sbe-cte-bench infra up --standard` and produces verified-equivalent containers; sharded topology comes up via `--sharded`. Health checks confirm replica sets initialized and journaling on. Resource-limit audit passes.

**Deliverables:**
- `src/sbe_cte_bench/infra/compose.py` — wraps `docker compose` invocations
- `src/sbe_cte_bench/infra/verify_limits.py` — queries `docker stats` and `docker inspect`, asserts equality
- `infra/compose.standard.yaml` and `infra/compose.sharded.yaml`
- `infra/Dockerfile.mongo-shard-router` (supervisord-orchestrated)
- `infra/oracle-bench.sql` and `infra/oracle-statspack.sql`
- CLI: `sbe-cte-bench infra up|down|verify [--topology=standard|sharded]`

**Tests written first:**
- `tests/integration/test_topology_standard.py::test_mongo_replica_set_initialized` — fail → implement compose.py + bring-up — pass
- `tests/integration/test_topology_standard.py::test_oracle_listens_on_1521`
- `tests/integration/test_topology_standard.py::test_journal_enabled` — `db.serverStatus().wiredTiger.log.maximum_log_file_size > 0`
- `tests/integration/test_topology_sharded.py::test_mongos_routes_through_two_shards`
- `tests/integration/test_resource_limits.py::test_cpus_enforced` — both containers report `--cpus="2.0"`
- `tests/integration/test_resource_limits.py::test_memory_enforced` — both containers report `--memory="4g"`

**Acceptance:**
- All P1 integration tests pass on the developer machine
- CI runs the *standard* topology subset (the sharded topology is gated behind a `slow` marker; runs nightly)
- `verify_limits` rejects mismatched containers (test by intentionally mis-sizing one)

### P2 — Data generation (4 days)

**Goal:** Deterministic data generator emits byte-stable BSON and CSV from a fixed seed. Scale factors SF0.1 and SF1 produce the entity counts the spec promises. The generator is independently testable without any database.

**Deliverables:**
- `src/sbe_cte_bench/data/schema.py` — Pydantic models for `Customer`, `Product`, `Category`, `Region`, `Supplier`, `Order`, `LineItem`, `S04Extension`, `S05Extension`
- `src/sbe_cte_bench/data/generator.py` — emits both BSON and CSV from a single source of truth using a deterministic PRNG (`numpy.random.Generator(PCG64(seed))`)
- `src/sbe_cte_bench/data/extensions.py` — S04 and S05 extensions
- `src/sbe_cte_bench/data/manifest.py` — SHA-256 of every output file, written to `data/generated/manifest.json`
- CLI: `sbe-cte-bench data generate --scale=SF1 [--include-extension=S04,S05]`

**Tests written first:**
- `tests/property/test_generator_determinism.py` — using hypothesis, generate at small scales with random seeds, assert two runs with same seed produce byte-identical output (file-by-file SHA-256 match)
- `tests/unit/test_schema_constraints.py` — asserts every generated entity validates against its Pydantic model
- `tests/unit/test_scale_factor_counts.py` — at SF0.1, exactly 100K orders / 10K customers / 1K products
- `tests/unit/test_extension_size.py` — S05 hot-customer extension produces exactly 20 customers × 800 orders × 30 line items
- `tests/golden/test_generator_byte_stability.py` — golden hash for SF0.001 (tiny CI-sized) committed in fixtures; test asserts current output matches

**Acceptance:**
- Generator deterministic under hypothesis fuzzing (≥ 1000 random-seed cases)
- SF0.001 golden hash matches across runs and across CI machines
- Manifest captures all hashes
- Generator runs SF1 in under 5 minutes locally

### P3 — Loading + indexing (3 days)

**Goal:** Generated data loads into both engines via the same CLI; indexes per scenario manifest are present and verified.

**Deliverables:**
- `src/sbe_cte_bench/data/load_mongo.py` — wraps `mongorestore`
- `src/sbe_cte_bench/data/load_oracle.py` — wraps `SQL*Loader` for relational entities, INSERT…SELECT with JSON_TRANSFORM for OSON
- `src/sbe_cte_bench/config/scenarios.py` — index manifest declarations per scenario
- CLI: `sbe-cte-bench data load --scale=SF1 [--scenario=Sxx]`

**Tests written first:**
- `tests/integration/test_load_round_trip.py::test_mongo_load_count_matches_generator` — generated 100K orders → loaded count is exactly 100K
- `tests/integration/test_load_round_trip.py::test_oracle_load_count_matches`
- `tests/integration/test_index_parity.py::test_s02_indexes_present_both_sides` — for each `mongo_indexes` entry, the corresponding `oracle_indexes` entry exists
- `tests/integration/test_index_used.py::test_s01_uses_expected_index` — runs `explain()` / `EXPLAIN PLAN` on a scenario query and asserts the expected index name appears in the plan

**Acceptance:**
- Round-trip integrity confirmed for SF0.1 in CI
- Index parity audit passes for all 15 scenarios
- Both engines report stats current after load

### P4 — Equivalence checking (3 days)

**Goal:** Result sets from Mongo and Oracle are canonicalized and compared by hash; mismatches produce actionable diffs. The equivalence module is the metamorphic differential test that the rest of the harness depends on.

**Deliverables:**
- `src/sbe_cte_bench/equivalence/canonicalize.py` — sort keys alphabetically; round floats to 9 decimal digits relative; convert `Decimal128` → `Decimal` → string; convert `bson.ObjectId` to `str`; sort arrays where the spec marks them as set-valued
- `src/sbe_cte_bench/equivalence/hash.py` — SHA-256 over the canonical JSON
- `src/sbe_cte_bench/equivalence/verify.py` — top-level: take two iterables of rows, canonicalize each, hash, compare, on mismatch produce a row-level diff for the first divergence

**Tests written first:**
- `tests/property/test_canonicalize_idempotent.py` — `canonicalize(canonicalize(x)) == canonicalize(x)` over hypothesis-generated nested structures (recursive strategy with leaves of `none|bool|int|float (no nan)|text|datetime`)
- `tests/property/test_canonicalize_order_invariant.py` — shuffling dict keys produces the same canonical form
- `tests/property/test_canonicalize_set_array.py` — when spec marks an array as set-valued, shuffling preserves equivalence
- `tests/unit/test_hash_distinct_inputs.py` — different result sets produce different hashes (anti-collision sanity)
- `tests/unit/test_hash_float_tolerance.py` — values within `1e-9` relative tolerance hash equal
- `tests/unit/test_diff_localizes_first_mismatch.py` — synthetic mismatch in row 47 produces a diff message naming row 47 + the differing field

**Acceptance:**
- ≥ 1000 hypothesis cases pass for idempotency and order-invariance
- 100% line and branch coverage on `equivalence/`
- Diff output is single-screen-readable for mismatches up to ~10 rows

### P5 — Drivers + connection management (2 days)

**Goal:** Thin, type-clean wrappers around `pymongo` and `python-oracledb` that handle connection pooling, SBE-flag verification, and statspack snapshot calls.

**Deliverables:**
- `src/sbe_cte_bench/drivers/mongo.py` — async-friendly wrapper; verifies `internalQueryFrameworkControl == "trySbeEngine"` before each scenario runs
- `src/sbe_cte_bench/drivers/oracle.py` — `python-oracledb` thin mode; takes `STATSPACK.SNAP` and runs `spreport.sql` via the canonical SQL*Plus pattern from research finding #11

**Tests written first:**
- `tests/integration/test_mongo_driver.py::test_framework_control_pinned`
- `tests/integration/test_mongo_driver.py::test_journal_enabled_assertion_fires`
- `tests/integration/test_oracle_driver.py::test_thin_mode_connects`
- `tests/integration/test_oracle_driver.py::test_statspack_snap_returns_id`
- `tests/integration/test_oracle_driver.py::test_statspack_report_writes_file`

**Acceptance:**
- Driver surface is fully typed (`mypy --strict` green)
- Pre-flight validation rejects classic-engine-by-default mongod
- Statspack snap-to-report cycle produces a non-empty report file

### P6 — Instrumentation (4 days)

**Goal:** Every scenario run captures the explain plan, the dbms_xplan, the statspack diff report, the OS counters, and the spill metrics in a structured form per the spec's run-record schema.

**Deliverables:**
- `src/sbe_cte_bench/observability/mongo_explain.py` — parses `executionStats`; identifies the *index of the stage* where `$cursor.queryPlanner` first appears (the SBE/classic boundary); flags `EXPRESS_IXSCAN` if it shows up
- `src/sbe_cte_bench/observability/oracle_xplan.py` — parses `dbms_xplan.display_cursor` text, extracts plan_hash, per-step operation, cardinality estimates, materialized-vs-inlined CTEs, workarea modes
- `src/sbe_cte_bench/observability/oracle_statspack.py` — wraps SQL*Plus invocation per research finding #11; parses spreport.sql output for top wait events and load profile
- `src/sbe_cte_bench/observability/spill_metrics.py` — extracts 8.1+ per-stage spill counters from `system.profile`
- `src/sbe_cte_bench/observability/os_counters.py` — reads `/proc/<pid>/status`, `getrusage`, cgroup v2 stats; namespaces per-container

**Tests written first:**
- `tests/unit/test_mongo_explain_parser.py::test_sbe_classic_boundary_index` — given a recorded explain JSON fixture with `$cursor` at stage 4, assert parsed `boundary_at_stage == 4`
- `tests/unit/test_mongo_explain_parser.py::test_express_path_detected`
- `tests/unit/test_oracle_xplan_parser.py::test_inlined_ctes_named` — given a fixed dbms_xplan output, assert each inlined CTE's name appears in the parsed `inlined_ctes` list
- `tests/unit/test_statspack_parser.py::test_top_wait_events_parsed`
- `tests/integration/test_os_counters.py::test_per_container_isolation` — verify counters distinguish mongo-bench from oracle-bench

**Acceptance:**
- All parsers tested against committed fixtures (recorded plans from real engine runs)
- Parsing handles real-world quirks: missing fields, version drift, optional sections
- Output schema validates against the run-record schema in `docs/07-reporting.md`

### P7 — Runner (3 days)

**Goal:** The timing loop. Warmup, alternating-system iteration, percentile computation, equivalence verification, run-record emission. Cold/warm cache management. Per-iteration timeout. CV check that flags noisy runs for re-execution.

**Deliverables:**
- `src/sbe_cte_bench/runner/timing.py` — `perf_counter_ns()` brackets, percentile math, no-mean-reporting
- `src/sbe_cte_bench/runner/warmup.py` — 3 warmup iterations + cache-clear hook
- `src/sbe_cte_bench/runner/alternating.py` — interleave mongo and oracle iterations
- `src/sbe_cte_bench/runner/scenario_runner.py` — top-level: take a `ScenarioSpec`, return a `RunRecord`
- `src/sbe_cte_bench/runner/concurrent.py` — multiprocess driver for S12

**Tests written first:**
- `tests/unit/test_percentile_math.py` — n=20 input, hand-checked p50/p95/p99, IQR, CV
- `tests/unit/test_warmup_discarded.py` — warmup timings appear in `warmup_ms[]` but never in `timings_ms[]`
- `tests/unit/test_alternating_order.py` — given mock systems, verify the interleaving sequence matches the spec
- `tests/integration/test_runner_smoke.py::test_s01_completes_at_sf0_001` — runs S01 at the smallest scale; asserts run record is schema-valid and equivalence passes
- `tests/unit/test_cv_threshold_flag.py` — synthetic noisy timings (cv > 0.10) raise the correct flag

**Acceptance:**
- S01 runs end-to-end via `uv run sbe-cte-bench run S01 --scale=SF0.001`
- Run record validates against the JSON schema
- Equivalence passes for S01 by construction

### P8 — Reporting (3 days)

**Goal:** Run records aggregated to summary CSV, per-scenario markdown writeups, and SVG charts. Charts are byte-stable across runs (matplotlib `svg.hashsalt` pinned).

**Deliverables:**
- `src/sbe_cte_bench/reporting/aggregate.py` — walks `results/raw/`, produces `results/processed/summary.csv`
- `src/sbe_cte_bench/reporting/plot.py` — generates the chart types from `docs/07-reporting.md` (latency-vs-knob bars, CDF, stacked spill, decision tree). Sets `mpl.rcParams['svg.hashsalt'] = 'sbe-cte-bench-v1'`
- `src/sbe_cte_bench/reporting/markdown.py` — per-scenario writeup generator
- `src/sbe_cte_bench/reporting/claim_coverage.py` — cross-scenario claim 11 summary

**Tests written first:**
- `tests/golden/test_chart_byte_stability.py` — given a fixed run record fixture, generated SVG is byte-identical to the committed golden SVG. Catches matplotlib upgrades that change rendering.
- `tests/unit/test_summary_csv_schema.py` — column set matches `docs/07-reporting.md`
- `tests/unit/test_markdown_per_scenario.py` — generated markdown contains: scenario title, predictions table, observed table, pass/fail verdict, link to run record
- `tests/unit/test_claim_coverage.py` — synthetic run records covering all 11 claims produce the expected per-claim summary

**Acceptance:**
- Charts byte-stable across the matplotlib version pinned in `pyproject.toml`
- Summary CSV passes its schema test
- Per-scenario markdown link-checks clean (all referenced run records exist)

### P9 — Scenarios (10 days)

**Goal:** All 15 scenarios implemented, each with a passing equivalence check at SF0.001 and SF0.1, predictions evaluated, run records produced.

**Implementation order** (drives by dependency and reuse):

1. **S01 baseline** — first; all other scenarios reuse this scaffolding
2. **S02 SBE prefix best case** — second; exercises the runner against multi-stage SBE
3. **S03 boundary tax** — third; introduces the variant-sweep pattern
4. **S04 stage wall** — introduces extension data
5. **S05 16MB cap** — introduces designed-failure handling
6. **S07 unsharded** — sets up recursive-CTE comparison code
7. **S08 windows** — reuses S03 variant pattern
8. **S09 predicate pushdown** — reuses S03 variant pattern
9. **S10 top-N** — reuses runner
10. **S11 OSON deep** — introduces depth sweep
11. **S13 planner stability** — introduces multi-scale data load
12. **S15 plan cache pollution** — introduces parameterized-shape generation
13. **S14 V14a/b** — introduces write-path; reuses standard topology
14. **S06 sharded $lookup** — introduces sharded topology lifecycle
15. **S07 sharded variant** — reuses S06 topology
16. **S14 V14c sharded** — reuses S06 topology
17. **S12 concurrent** — last; depends on all other scenarios for the workload mix

**Per-scenario TDD pattern:**
- `tests/e2e/test_sxx.py::test_runs_at_sf0_001` — scenario completes; equivalence hash matches; run record valid
- `tests/e2e/test_sxx.py::test_predictions_evaluable` — prediction blocks produce explicit pass/fail outcomes
- `tests/e2e/test_sxx.py::test_explain_plan_signature` — for scenarios that test specific architectural phenomena, the *qualitative* signature is asserted (e.g., S03 asserts `boundary_at_stage > 0`)

**Acceptance per scenario:**
- E2E test passes at SF0.001 in CI
- E2E test passes at SF0.1 on developer machine (manual verification)
- Run record schema-valid
- Equivalence hashes match

**Acceptance for the phase:**
- All 15 scenarios pass at SF0.001 in nightly CI
- 13 scenarios pass at SF1 on the reference developer machine (S04, S05 expected to take longer; not gated)

### P10 — Polish + v1.0 release (3 days)

**Goal:** A clean v1.0 tag with a reproducibility manifest. Documentation reviewed against the spec. CI green on a clean clone.

**Deliverables:**
- Documentation pass: every doc reviewed for staleness; all command examples copy-paste runnable
- Reproducibility manifest: a script that takes a published result and re-runs it to confirm match
- `CHANGELOG.md` initialized
- `LICENSE` file (Apache 2.0 unless author overrides)
- `v1.0.0` git tag

**Tests:**
- `tests/e2e/test_reproducibility.py::test_published_result_reproduces` — given a committed reference run record, re-running the scenario produces equivalent results within tolerance

**Acceptance:**
- Fresh clone → `uv sync && uv run sbe-cte-bench all --scale=SF0.001` completes
- All CI workflows green
- v1.0.0 tag created

### Total estimate

~40 working days for one full-time engineer. With buffer: 8–10 weeks.

---

## 5. TDD workflow

Strict TDD per phase. The cycle:

```
red → green → refactor → commit
```

**Concretely, for every new module/function:**

1. **Write the failing test first** in the appropriate `tests/` subdirectory.
2. **Commit the failing test** with a clear message: `test(equivalence): canonicalize is idempotent (failing)`. This is a deliberate "red" commit. CI runs and fails.
3. **Write the minimum code** to make the test pass. Resist over-engineering. No imaginary requirements.
4. **Commit the passing implementation** with a message: `feat(equivalence): canonicalize idempotent for nested dicts`.
5. **Refactor** if needed (extract helper, rename, etc.). Tests must stay green.
6. **Commit refactor** if applicable: `refactor(equivalence): extract dict-key sort helper`.

**The PR template requires showing the red → green commit pair**, or a single commit with both the test and the implementation if the change is small enough that splitting is contrived.

### What gets unit-tested vs integration-tested vs e2e-tested

| Module | Test type | Rationale |
|--------|-----------|-----------|
| `equivalence/` | Unit + property | Pure logic; no IO. Hypothesis-fuzzed. 100% coverage. |
| `data/schema.py` | Unit | Pydantic models; schema validation. |
| `data/generator.py` | Property + golden | Determinism via hypothesis; byte-stability via golden hashes. |
| `data/load_*.py` | Integration | Requires running containers; tested at SF0.001 in CI. |
| `infra/compose.py` | Integration | Requires Docker; tested via testcontainers. |
| `infra/verify_limits.py` | Unit + integration | Logic unit-testable on mock `docker stats` JSON; full path integration-tested. |
| `drivers/*` | Integration | Requires running engines. |
| `observability/*_parser.py` | Unit | Parsers operate on captured fixtures; pure logic. |
| `observability/os_counters.py` | Integration | Reads /proc and cgroup; needs real container. |
| `runner/timing.py` | Unit | Percentile math is pure. |
| `runner/scenario_runner.py` | Integration | Orchestration; needs containers. |
| `scenarios/sxx_*.py` | E2E | Full scenario at SF0.001; equivalence + schema-valid run record. |
| `reporting/plot.py` | Golden | Byte-stable SVG vs committed golden. |
| `reporting/aggregate.py` | Unit | Operates on synthetic run records. |

### Property-based testing

Used liberally for any module where a logical invariant exists:

- Canonicalization idempotency, order-invariance, set-array equivalence
- Generator determinism (same seed → same output)
- Hash distinctness (different inputs → different hashes)
- Percentile math bounds (`p50 ≤ p95 ≤ p99 ≤ max`)
- Run-record schema (any well-formed run record validates)

Hypothesis settings: `@settings(deadline=None, max_examples=200, suppress_health_check=[HealthCheck.too_slow])` for IO-touching tests; default for pure-logic.

### What does NOT get tested

- Latency thresholds in CI. Wall-clock numbers in CI mean nothing — runners are noisy. The harness produces a run record; the *interpretation* of those numbers is for dedicated hardware.
- Third-party library internals. We don't test that pymongo's `aggregate()` returns a cursor; we test our wrapper around it.
- Plot aesthetics. Charts are byte-stable for the *committed* golden inputs; visual quality is a manual review item, not a gate.

---

## 6. `pyproject.toml` reference

```toml
[project]
name = "sbe-cte-bench"
version = "0.1.0.dev0"
description = "Benchmark framework comparing MongoDB SBE aggregation pipeline to Oracle nested CTEs over JSON_TABLE / JSON Duality Views."
readme = "README.md"
authors = [
  { name = "Rick Houlihan", email = "rick.houlihan@gmail.com" },
]
license = { text = "Apache-2.0" }
requires-python = "==3.12.*"
dependencies = [
  "pymongo>=4.10,<5",
  "oracledb>=2.4,<3",
  "pydantic>=2.9,<3",
  "numpy>=2.1,<3",
  "matplotlib>=3.9,<4",
  "seaborn>=0.13,<0.14",
  "click>=8.1,<9",
  "rich>=13.9,<14",
  "structlog>=24.4,<25",
  "python-bson>=4.10,<5",   # transitive via pymongo, pinned for safety
]

[project.scripts]
sbe-cte-bench = "sbe_cte_bench.cli:main"

[dependency-groups]
dev = [
  "ruff>=0.7,<0.9",
  "mypy>=1.13,<2",
  "pre-commit>=4.0,<5",
  "pytest>=8.3,<9",
  "pytest-cov>=6.0,<7",
  "pytest-xdist>=3.6,<4",
  "pytest-timeout>=2.3,<3",
  "hypothesis>=6.115,<7",
  "testcontainers>=4.8,<5",
  "freezegun>=1.5,<2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sbe_cte_bench"]

# ─── Ruff ───────────────────────────────────────────────────────────────
[tool.ruff]
target-version = "py312"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = [
  "E",    # pycodestyle errors
  "F",    # pyflakes
  "W",    # pycodestyle warnings
  "I",    # isort
  "B",    # flake8-bugbear
  "UP",   # pyupgrade
  "SIM",  # simplify
  "PTH",  # use pathlib
  "RUF",  # ruff-specific
  "C4",   # comprehensions
  "PIE",  # misc anti-patterns
  "PT",   # pytest style
  "TID",  # tidy imports
  "ARG",  # unused arguments
  "ERA",  # commented-out code
  "S",    # bandit (security)
  "N",    # pep8-naming
]
ignore = [
  "S101",  # assert is fine in tests
  "S603",  # subprocess: we audit our subprocess calls
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["ARG", "S"]

[tool.ruff.lint.isort]
known-first-party = ["sbe_cte_bench"]

# ─── Mypy ───────────────────────────────────────────────────────────────
[tool.mypy]
python_version = "3.12"
strict = true
warn_unused_ignores = true
warn_redundant_casts = true
files = ["src", "tests"]

[[tool.mypy.overrides]]
module = ["oracledb.*", "pymongo.*", "bson.*", "testcontainers.*", "matplotlib.*", "seaborn.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_decorators = false  # pytest fixtures are often untyped at the API surface

# ─── Pytest ─────────────────────────────────────────────────────────────
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
markers = [
  "unit: pure-logic tests, no Docker, no network",
  "integration: requires Docker/containers",
  "property: hypothesis-driven property tests",
  "golden: golden-file byte-stability tests",
  "e2e: full scenario at SF0.001 (CI-sized)",
  "slow: heavy/long-running; nightly only",
  "sharded: requires sharded mongo topology",
]
addopts = [
  "-ra",
  "--strict-markers",
  "--strict-config",
  "--showlocals",
  "--tb=short",
]
timeout = 120
timeout_method = "thread"

# ─── Coverage ──────────────────────────────────────────────────────────
[tool.coverage.run]
source = ["src/sbe_cte_bench"]
branch = true
omit = [
  "src/sbe_cte_bench/infra/compose.py",   # integration-only
  "src/sbe_cte_bench/infra/topology_swap.py",
]
parallel = true

[tool.coverage.report]
fail_under = 80
show_missing = true
skip_covered = false
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if TYPE_CHECKING:",
  "if __name__ == .__main__.:",
]

# ─── uv ────────────────────────────────────────────────────────────────
[tool.uv]
package = true
```

---

## 7. Pre-commit hooks reference

`.pre-commit-config.yaml`:

```yaml
default_language_version:
  python: python3.12

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: detect-private-key
      - id: mixed-line-ending
        args: ["--fix=lf"]

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.4
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies:
          - "pydantic>=2.9"
          - "numpy>=2.1"
          - "click>=8.1"
        args: ["--config-file=pyproject.toml"]
        files: ^src/

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks

  - repo: https://github.com/shellcheck-py/shellcheck-py
    rev: v0.10.0.1
    hooks:
      - id: shellcheck
        args: ["--severity=warning"]

  - repo: https://github.com/abravalheri/validate-pyproject
    rev: v0.22
    hooks:
      - id: validate-pyproject

  # Local hooks: fast unit tests only on modified files
  - repo: local
    hooks:
      - id: pytest-fast
        name: pytest (unit, fast)
        entry: uv run pytest -m "unit and not slow" -q --no-cov
        language: system
        types: [python]
        pass_filenames: false
        stages: [pre-push]   # only on push; per-commit is too slow
```

The fast unit tests are scoped to the `pre-push` stage, not `pre-commit`. Per-commit hooks must be sub-second; per-push hooks can take a few seconds. This is the pragmatic split that keeps developers from bypassing hooks.

`.gitleaks.toml` — minimal, allows the `BENCH_PASSWORD` variable name:

```toml
[allowlist]
description = "Test passwords are deliberately committed in CI fixtures"
regexes = [
  '''BenchPass2026''',          # documented test password
  '''ORACLE_PWD=BenchPass2026''',
]
```

---

## 8. CI/CD pipeline reference

Three workflows.

### `.github/workflows/ci.yml` — fast feedback on every push

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

env:
  UV_VERSION: "0.5.4"

jobs:
  lint-and-types:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen --all-groups
      - run: uv run ruff check src/ tests/
      - run: uv run ruff format --check src/ tests/
      - run: uv run mypy src/ tests/
      - run: uv run validate-pyproject pyproject.toml

  unit:
    runs-on: ubuntu-24.04
    needs: lint-and-types
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen --all-groups
      - run: uv run pytest -m "unit or property or golden" --cov --cov-report=xml -n auto
      - uses: codecov/codecov-action@v4
        with:
          files: ./coverage.xml
          fail_ci_if_error: false   # codecov outages should not block PRs

  integration-standard:
    runs-on: ubuntu-24.04
    needs: unit
    services:
      mongo:
        image: mongodb/mongodb-community-server:8.2.2-ubuntu2404
        ports:
          - 27017:27017
        options: >-
          --health-cmd "mongosh --eval 'db.runCommand({ping:1})'"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 30
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - uses: gvenzl/setup-oracle-free@v1
        with:
          tag: "26-faststart"
          app-user: BENCH
          app-user-password: BenchPass2026
      - run: uv sync --frozen --all-groups
      - run: uv run pytest -m "integration and not sharded and not slow" -n 2

  e2e-smoke:
    runs-on: ubuntu-24.04
    needs: integration-standard
    services:
      mongo:
        image: mongodb/mongodb-community-server:8.2.2-ubuntu2404
        ports: ["27017:27017"]
        options: >-
          --health-cmd "mongosh --eval 'db.runCommand({ping:1})'"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 30
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - uses: gvenzl/setup-oracle-free@v1
        with:
          tag: "26-faststart"
          app-user: BENCH
          app-user-password: BenchPass2026
      - run: uv sync --frozen --all-groups
      - run: uv run sbe-cte-bench data generate --scale=SF0.001
      - run: uv run sbe-cte-bench data load --scale=SF0.001
      - run: uv run sbe-cte-bench run S01 --scale=SF0.001
      # Smoke: verify the run record validates and the equivalence hash is non-empty.
      - run: uv run python -m sbe_cte_bench.tools.validate-run-record results/raw/*.json

  reproducibility:
    runs-on: ubuntu-24.04
    needs: unit
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen --all-groups
      # Generator determinism: produce twice, assert byte-stable.
      - run: uv run sbe-cte-bench data generate --scale=SF0.001 --output-dir=/tmp/run1
      - run: uv run sbe-cte-bench data generate --scale=SF0.001 --output-dir=/tmp/run2
      - run: diff -r /tmp/run1 /tmp/run2
```

### `.github/workflows/nightly.yml` — heavy integration, runs once daily

```yaml
name: nightly

on:
  schedule:
    - cron: "0 5 * * *"   # 05:00 UTC daily
  workflow_dispatch:

jobs:
  e2e-full:
    # Self-hosted runner with sufficient RAM for sharded topology
    # (GitHub-hosted runners cap at 7 GB; sharded mongo + oracle eats more).
    runs-on: [self-hosted, sbe-cte-bench-runner]
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.4"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen --all-groups
      # Run all e2e tests including sharded scenarios at SF0.01.
      - run: uv run pytest -m "e2e or sharded" --timeout=3600
      - uses: actions/upload-artifact@v4
        with:
          name: nightly-results
          path: results/
          retention-days: 30
```

### `.github/workflows/release.yml` — on tag, validate reproducibility

```yaml
name: release

on:
  push:
    tags: ["v*"]

jobs:
  validate-reproducibility:
    runs-on: [self-hosted, sbe-cte-bench-runner]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.4"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen --all-groups
      # Take the published reference run record (committed in tests/golden/)
      # and re-run; assert equivalence within tolerance.
      - run: uv run pytest tests/e2e/test_reproducibility.py -v
      - uses: actions/upload-artifact@v4
        with:
          name: release-${{ github.ref_name }}-validation
          path: results/
```

### Self-hosted runner setup

For the nightly and release jobs, a self-hosted runner is required because:

- GitHub-hosted runners have 7 GB RAM; sharded mongo (2 shards + cfgsvr + mongos) + Oracle Free + harness ≈ 12+ GB.
- Wall-clock timing on GitHub-hosted shared infrastructure is meaningless. Even nightly correctness checks at SF0.01 are *correctness* checks, not benchmark numbers — but having stable hardware reduces noise.
- The `release` job's reproducibility validation must run on a known reference machine.

Self-hosted runner specs match the spec's reference hardware in `docs/02-infrastructure.md`:
- 8+ physical cores, 16+ GB RAM, 100+ GB local NVMe
- Ubuntu 24.04 LTS
- Docker installed
- Tagged `sbe-cte-bench-runner` so workflows can target it

The runner is **only** for this benchmark — no other workloads, to avoid neighbor noise.

---

## 9. Test strategy

### Test pyramid (target proportions)

```
        ╱─╲
       ╱e2e╲          ~5% — full scenarios at SF0.001; ~15 tests
      ╱─────╲
     ╱integ. ╲        ~15% — Docker required; ~50 tests
    ╱─────────╲
   ╱property &  ╲    ~20% — hypothesis-fuzzed; ~20 tests with broad case coverage
  ╱   golden     ╲
 ╱─────────────────╲
╱       unit        ╲ ~60% — pure logic; ~150 tests
─────────────────────
```

### Markers

- `unit` — pure logic; no Docker, no network. Runs in <30s total.
- `integration` — requires Docker/containers; runs against testcontainers fixtures.
- `property` — hypothesis-driven; can be slow at high `max_examples`.
- `golden` — golden-file byte-stability; very fast.
- `e2e` — full scenario at SF0.001; runs in 30s–5min per scenario.
- `slow` — long-running; nightly-only.
- `sharded` — requires sharded mongo topology; nightly-only.

### Fixtures

Top-level `tests/conftest.py`:

- `mongo_standard` — session-scoped testcontainers fixture for the standard mongod replica set
- `oracle_free` — session-scoped testcontainers fixture for `gvenzl/oracle-free`
- `mongo_sharded` — session-scoped, marker-gated to `sharded`
- `tiny_data` — session-scoped, generates SF0.001 data once and loads into both engines
- `clean_caches` — function-scoped, clears plan caches and OS page cache between iterations

### Hypothesis settings

```python
# tests/conftest.py
from hypothesis import settings, HealthCheck

settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    max_examples=50,
)
settings.register_profile(
    "thorough",
    parent=settings.get_profile("default"),
    max_examples=1000,
)
settings.load_profile("ci" if os.environ.get("CI") else "default")
```

Run with `HYPOTHESIS_PROFILE=thorough` for nightly verification.

### Golden files

Stored under `tests/golden/`, committed in git:
- `generator_sf0_001_manifest.json` — committed hashes of the SF0.001 generator output
- `mongo_explain_s03_k4.json` — recorded explain output for the S03 boundary-position-4 case
- `oracle_xplan_s02.txt` — recorded dbms_xplan output for S02
- `chart_s03_boundary_tax.svg` — byte-stable chart for the S03 reference run record

Updating a golden file requires:
1. Justify why in the PR description (engine version bump, intentional plot redesign, etc.).
2. The PR shows the diff visually (for SVGs, attach a screenshot).
3. A reviewer signs off on the goldens-update commit explicitly.

This is the discipline that catches accidental drift.

---

## 10. Quality gates

Pass criteria for merging to `main`:

| Gate | Threshold | Enforced by |
|------|-----------|-------------|
| Ruff lint | zero violations | CI `lint-and-types` |
| Ruff format | clean | CI `lint-and-types` |
| Mypy strict | zero errors | CI `lint-and-types` |
| Unit + property + golden tests | 100% pass | CI `unit` |
| Coverage on `equivalence/` | ≥ 95% line, ≥ 95% branch | CI `unit` |
| Coverage on `data/generator.py` | ≥ 95% line | CI `unit` |
| Coverage overall | ≥ 80% line | CI `unit` |
| Integration (standard topology) | 100% pass | CI `integration-standard` |
| E2E smoke (S01 at SF0.001) | passes; equivalence hash matches | CI `e2e-smoke` |
| Generator reproducibility | byte-identical output across two runs | CI `reproducibility` |
| Validate pyproject | clean | pre-commit + CI |
| Gitleaks | no findings | pre-commit + CI |
| Sharded e2e | 100% pass nightly | CI `nightly` |

A PR that fails any gate is not mergeable. Quality gates are configured as **required status checks** in branch protection.

### Coverage gates by module

```
src/sbe_cte_bench/equivalence/   ≥ 95% line, ≥ 95% branch
src/sbe_cte_bench/data/          ≥ 90% line (heavy IO subdirs excluded)
src/sbe_cte_bench/observability/ ≥ 80% line (parsers ≥95%, OS counters skipped)
src/sbe_cte_bench/runner/        ≥ 85% line
src/sbe_cte_bench/scenarios/     ≥ 70% line (e2e-tested, not unit-tested)
src/sbe_cte_bench/reporting/     ≥ 90% line
src/sbe_cte_bench/infra/         excluded — integration-only
src/sbe_cte_bench/drivers/       excluded — integration-only
```

Configured via `[tool.coverage.run] omit` and per-module `fail_under` overrides.

### Branch protection

- `main` requires:
  - All CI status checks green
  - At least 1 approving review (or self-approval if solo author, with explicit acknowledgment in commit)
  - No force-pushes
  - No deletions
  - Linear history (rebase, not merge commits)
- Tags (`v*`) require the `release` workflow to succeed before the tag is signed.

---

## 11. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Oracle Free's 12 GB cap changes between 26ai patch versions | Low | High (data load fails) | Pin image digest; CI fails fast on load; alert in release notes |
| `gvenzl/oracle-free` image discontinued | Low | Medium | Mirror digest to internal registry; document fallback to official image with CI auth |
| MongoDB 8.x SBE behavior changes in a patch release | Medium | Medium | Pin to `8.2.2-ubuntu2404` exactly; document upgrade testing protocol |
| Matplotlib SVG rendering changes break golden tests | Medium | Low | Pin matplotlib version; `svg.hashsalt` fixed; document the upgrade-goldens flow |
| GitHub Actions service container can't host Oracle (image pull) | Low | High | Already mitigated: use `gvenzl/setup-oracle-free` action |
| Self-hosted runner becomes unavailable | Medium | High | Document the runner setup procedure; have a documented fallback to a developer machine for release validation |
| Hypothesis finds a canonicalization edge case after release | High | Medium | Hypothesis has `@example` regression decorators; pin failing case as an example, fix, ship |
| Equivalence hashes mismatch due to a real semantic difference between engines | Medium | High (a scenario can't ship) | Scenario-by-scenario triage; either rewrite the SQL/pipeline to reconcile, or document the divergence in the scenario spec |
| Statspack output format changes between Oracle 26.x patch versions | Low | Medium | Parser is forgiving on optional sections; CI parses captured fixtures |
| `uv.lock` resolution changes after a transitive bumps | Medium | Low | `uv lock --upgrade` is a deliberate, auditable PR |
| Test flake on testcontainers under load | Medium | Low | Retry once with backoff; fail clearly on second flake; flake monitoring via repeated CI runs |
| Disk full during S04/S05 spill | Medium | Medium | Pre-iteration disk check; fail fast with a clear error if `/var/lib/docker` has < 30 GB free |

---

## 12. Definition of done

A scenario is **done** when:

- [ ] Implementation matches the spec in `docs/scenarios/Sxx-*.md`
- [ ] Equivalence hash matches between Mongo and Oracle results at SF0.001 and SF0.1
- [ ] Run record validates against the JSON schema in `docs/07-reporting.md`
- [ ] All declared predictions are evaluable (each has an explicit pass/fail outcome in the run record)
- [ ] Per-scenario unit and integration tests pass
- [ ] E2E test passes in nightly CI at SF0.001
- [ ] At least one full SF1 run completed on reference hardware; numbers recorded in `results/processed/scenario-Sxx.md`
- [ ] Per-scenario writeup generated and reviewed
- [ ] Coverage meets the per-module threshold

The benchmark is **v1.0 done** when:

- [ ] All 15 scenarios are done per the per-scenario checklist
- [ ] Cross-scenario claim 11 summary generated and reviewed
- [ ] All quality gates green
- [ ] Reproducibility manifest validated by `release` workflow
- [ ] `CHANGELOG.md` entry for v1.0
- [ ] `LICENSE` committed
- [ ] `v1.0.0` tag pushed and `release` workflow succeeds
- [ ] README.md "Status" line updated from "drafted" to "v1.0 released"
- [ ] One end-to-end fresh-clone reproducibility test passed by an external reviewer

---

## Appendix A — Why TDD specifically here

This is a benchmark *framework*. The numbers it produces will inform an article that takes specific positions about engine architecture. Wrong numbers are worse than no numbers — a result that's later retracted is a credibility hit that never fully recovers.

TDD is the only discipline that scales to "I can defend every component of this harness because I can show the test that motivates it." It is slower in calendar days. It is faster in *correct* calendar days.

The equivalence checker in particular *must* be TDD'd. It's the metamorphic differential test that gives the entire benchmark its credibility. If two engines produce different results and the harness reports "match" because of a canonicalization bug, the benchmark publishes wrong numbers. Property-based testing of the canonicalizer is non-negotiable.

## Appendix B — Why the gvenzl image and not the official Oracle image

The official `container-registry.oracle.com/database/free:26ai` image requires Oracle SSO authentication to pull. In a public-CI context this means storing Oracle credentials in repository secrets and fighting the rate limiter. The image works fine for human use; it's hostile to automation.

`gvenzl/oracle-free` is the same Oracle Free Edition binary, packaged into a Docker Hub image without the SSO requirement. It's maintained by Gerald Venzl, an Oracle employee, on his own time as a community resource. It tracks Oracle Free releases (currently 23ai; 26ai support follows shortly after each release). The images are byte-identical to the official ones in their database content; only the wrapper differs.

For CI: gvenzl is the pragmatic choice. For published reference runs on dedicated hardware: either is fine; the spec calls out the official image as the primary target so users can verify against the canonical Oracle artifact if they want.

## Appendix C — Self-hosted runner alternative

If a self-hosted runner is unavailable, the nightly and release workflows can run against an OCI Always-Free VM with the gvenzl image — but timings will be variable. The CI design separates *correctness* (which CI gates) from *benchmark numbers* (which require dedicated hardware) precisely so that this fallback is acceptable: CI without a dedicated runner still validates the harness, just doesn't validate timing reproducibility.

A "documented reference run" in `results/processed/` always cites the hardware it ran on. Anyone reproducing it on a different machine compares their numbers against the stated hardware delta.
