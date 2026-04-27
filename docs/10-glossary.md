# 10 — Glossary

Terms used throughout the spec, in alphabetical order. Where a term is system-specific, the column header is given.

| Term | System | Meaning |
|------|--------|---------|
| Boundary tax | (this benchmark) | The per-document materialization cost paid for every stage downstream of the first SBE-incompatible stage in a MongoDB pipeline. The central phenomenon S03 measures. |
| BSON | MongoDB | Binary JSON. Length-prefixed, sequential traversal. O(n) field access. |
| CBO | Oracle | Cost-Based Optimizer. Plans queries by estimating the cost of alternatives using statistics. |
| Classic engine | MongoDB | The pre-SBE aggregation execution engine. Materializes a `Document` per row per stage. Still runs all stages SBE doesn't push down. |
| CTE | Oracle | Common Table Expression. A named query block in a `WITH` clause. Inlined or materialized depending on reference count and hints. |
| Document source | MongoDB | The C++ class hierarchy implementing aggregation stages in the classic engine. `DocumentSource::getNext()` is the per-row pull interface. |
| Express Path | MongoDB | 8.0+ optimization that bypasses multi-planning for IDHACK-eligible point queries. Visible in explain as `EXPRESS_IXSCAN`. |
| FPTP | MongoDB | First-Past-the-Post. The classic plan-selection algorithm: race candidate plans for a small number of works, pick the first to return enough rows. Reference: arXiv 2409.16544. |
| Iterator tree | (general) | The execution tree built from row-source operators in a Volcano-model engine. Each node implements `open`/`fetch`/`close`. Oracle's execution model. |
| JDV | Oracle | JSON-Relational Duality View. A bidirectional mapping between normalized base tables and a JSON document shape. |
| `JSON_TABLE` | Oracle | SQL/JSON function that projects a JSON value into relational rows and columns. Lateral row source on the right side of a join. |
| Lowering | MongoDB | The process of translating a `DocumentSource` (classic-engine stage) into SBE bytecode. Stages that lower successfully run in SBE. |
| Materialization (CTE) | Oracle | The CBO's choice to write a CTE's result into a temp segment and read from it. Default for multi-reference CTEs. Forced by `/*+ MATERIALIZE */`. |
| Multi-pass / one-pass / optimal | Oracle | Workarea execution modes. Optimal: fully in PGA. One-pass: spills once to temp. Multi-pass: spills multiple times. Tracked in `v$sql_workarea_active`. |
| OSON | Oracle | Oracle's binary JSON format. Hash-indexed tree with O(1) field access. |
| Plan cache | (general, system-specific) | The cache of compiled query plans keyed by query shape. Mongo: per-collection, FPTP-trip-wired. Oracle: shared cursor cache, bind-aware. |
| Pushdown (SBE) | MongoDB | The act of an aggregation stage being executed by SBE rather than the classic engine. |
| Pushdown (predicate) | (general) | Moving a filter predicate closer to the data source so fewer rows propagate up the plan. CBO does this routinely; MongoDB does it only across certain stage boundaries. |
| RAC | Oracle | Real Application Clusters. Multi-node Oracle running against shared storage. |
| Row source | Oracle | A pull-mode operator in the iterator tree. Returns rows on demand to its parent. |
| SBE | MongoDB | Slot-Based Executor. The 5.0+ replacement for the classic aggregation engine for a subset of stages. Operates on slot values rather than materialized documents. |
| Scatter-gather | MongoDB (sharded) | The pattern of issuing a query to all shards holding a target collection and joining the results in memory. The execution model `$lookup` falls back to on sharded foreigns. |
| Sharded foreign | MongoDB | A `$lookup` whose `from` collection is sharded. Triggers SBE pushdown disqualification. |
| Single iterator tree | Oracle | Oracle's execution-model property: nested CTEs inline into a single tree of row-source operators with no per-CTE materialization boundary. The architectural property the article highlights. |
| Slot (SBE) | MongoDB | Named storage cell shared between SBE operators. The thing that replaces full document materialization between stages within SBE-eligible territory. |
| Smart Scan | Oracle (Exadata) | Storage-cell-side filter and projection offload. Out of scope for this benchmark (commodity hardware). |
| Spill | (general) | Writing operator state to disk because it exceeds the available memory. Mongo: `$group`/`$sort`/`$bucketAuto`/`$setWindowFields` spill at 100 MB per stage. Oracle: workarea-bounded; spills to temp tablespace per workarea grant. |
| TAC | Oracle | Transparent Application Continuity. Runtime-level continuation of in-flight transactions across failover. Out of scope. |
| Statspack | Oracle | Free, included-since-8i system-wide performance repository. The AWR-equivalent for Oracle Free Edition (which does not include the Diagnostic Pack). Captures snapshot pairs and produces diff reports of wait events, load profile, top SQL, tablespace IO. |
| Volcano model | (general) | Pull-based, iterator-tree query execution model from Graefe 1994. Oracle's execution architecture. |
| WiredTiger | MongoDB | The default MongoDB storage engine. Page-cache-backed B-tree storage. Cache size set by `wiredTigerCacheSizeGB`. |
| Working set | (general) | The set of pages an operator needs in memory to execute. Mongo: per-stage 100 MB cap. Oracle: workarea-driven, sized by CBO + `PGA_AGGREGATE_TARGET`. |

## Acronyms

- **ADB** — Autonomous Database (Oracle Cloud).
- **AWR** — Automatic Workload Repository (Oracle Diagnostic Pack feature; **not available on Free**).
- **ASH** — Active Session History (Oracle Diagnostic Pack feature; **not available on Free**).
- **CBO** — Cost-Based Optimizer.
- **CTE** — Common Table Expression.
- **FPTP** — First-Past-the-Post.
- **JDV** — JSON-Relational Duality View.
- **OSON** — Oracle's binary JSON format.
- **PGA** — Program Global Area (Oracle per-session memory).
- **RAC** — Real Application Clusters.
- **SBE** — Slot-Based Executor.
- **SF** — Scale Factor.
- **SGA** — System Global Area (Oracle shared memory).
- **TAC** — Transparent Application Continuity.
- **TPC-H** — Transaction Processing Council Decision Support benchmark.
- **WT** — WiredTiger.
