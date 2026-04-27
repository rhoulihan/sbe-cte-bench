"""S07 — recursive traversal: ``$graphLookup`` vs ``CONNECT BY``.

Tests two architectural properties of recursive graph workloads at SF1 scale.

1. **Depth scaling** (``org-d{N}``): how cost grows with traversal depth in
   a 100K-employee org tree (branching=5, saturates around depth 8).
   Mongo's ``$graphLookup`` is **classic-only** — no SBE optimization, no
   plan choice, single-threaded BFS one level per server-side iteration
   with a materialized frontier between levels. Oracle's ``CONNECT BY``
   collapses to a single hash-driven descendant walk under the CBO.

2. **Path materialization** (``path-d{N}``): per-level aggregation, not
   just enumeration. Oracle's ``CONNECT BY`` exposes ``LEVEL`` natively
   for one-pass depth bucketing; Mongo has to ``$graphLookup`` then
   ``$unwind`` + ``$group`` over the materialized subtree.

Note: the original S07 design also included BOM-quantity-rollup and
cycle-detection variants. Those were dropped because (a) BOM rollup with
path-product multiplication is structurally not expressible in ``$graphLookup``
(the engine cannot propagate values along the recursion path), making
equivalence-checked benchmarking impossible; (b) cycle detection at the
sparse-graph scale we tested was a per-query latency comparison, not an
architectural one. The remaining variants test the architectural property
at a scale where it actually manifests.

**Key SQL choice:** Oracle queries use ``CONNECT BY`` rather than the
SQL-standard ``WITH RECURSIVE``. Empirical finding: at SF1, ``CONNECT BY``
is 6×-477× faster than ``WITH RECURSIVE`` for the same logical workload.
Recursive CTEs materialize each iteration as a true intermediate relation
(write-then-read dam between every level); ``CONNECT BY`` keeps the
frontier in a single hash table and streams. The Oracle CBO has 30+
years of tuning for ``CONNECT BY`` specifically.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


_ORG_ROOT = 1


@register
class S07Recursive(ScenarioBase):
    id: ClassVar[str] = "S07"
    title: ClassVar[str] = "Recursive traversal: $graphLookup vs CONNECT BY"
    primary_collection: ClassVar[str] = "employees"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            # Depth scaling — org subtree subordinate-count + salary rollup.
            Variant(label="org-d2", parameters={"family": "org", "depth": 2}),
            Variant(label="org-d5", parameters={"family": "org", "depth": 5}),
            Variant(label="org-d10", parameters={"family": "org", "depth": 10}),
            Variant(label="org-d15", parameters={"family": "org", "depth": 15}),
            # Path materialization — per-level node count under root.
            Variant(label="path-d5", parameters={"family": "path", "depth": 5}),
            Variant(label="path-d10", parameters={"family": "path", "depth": 10}),
        ]

    # ── Mongo pipelines ──────────────────────────────────────────────────

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        v = variant or Variant(label="org-d5", parameters={"family": "org", "depth": 5})
        family = v.parameters["family"]
        depth = int(v.parameters["depth"])

        if family == "org":
            return cls._mongo_org(depth)
        if family == "path":
            return cls._mongo_path(depth)
        raise ValueError(f"unknown family {family!r}")

    @classmethod
    def _mongo_org(cls, depth: int) -> list[dict[str, Any]]:
        # Subordinates within ``depth`` levels under root, with subtree
        # salary sum + count. Output: one row, two scalars.
        return [
            {"$match": {"employee_id": _ORG_ROOT}},
            {
                "$graphLookup": {
                    "from": "employees",
                    "startWith": "$employee_id",
                    "connectFromField": "employee_id",
                    "connectToField": "manager_id",
                    "maxDepth": depth - 1,  # 0-indexed
                    "as": "subordinates",
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "subordinate_count": {"$size": "$subordinates"},
                    "subtree_salary": {
                        "$round": [{"$sum": "$subordinates.salary"}, 2]
                    },
                }
            },
        ]

    @classmethod
    def _mongo_path(cls, depth: int) -> list[dict[str, Any]]:
        # Count nodes at each depth level under the root, up to ``depth``
        # levels. ``depthField`` is 0-indexed for direct children; we add
        # 1 to align with Oracle's 1-indexed LEVEL semantics in the
        # equivalence check.
        return [
            {"$match": {"employee_id": _ORG_ROOT}},
            {
                "$graphLookup": {
                    "from": "employees",
                    "startWith": "$employee_id",
                    "connectFromField": "employee_id",
                    "connectToField": "manager_id",
                    "maxDepth": depth - 1,
                    "depthField": "depth0",
                    "as": "subtree",
                }
            },
            {"$unwind": "$subtree"},
            {
                "$group": {
                    "_id": {"$add": ["$subtree.depth0", 1]},
                    "node_count": {"$sum": 1},
                }
            },
            {"$project": {"_id": 0, "lvl": "$_id", "node_count": 1}},
            {"$sort": {"lvl": 1}},
        ]

    # ── Oracle SQL ───────────────────────────────────────────────────────

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        v = variant or Variant(label="org-d5", parameters={"family": "org", "depth": 5})
        family = v.parameters["family"]
        depth = int(v.parameters["depth"])

        if family == "org":
            return cls._oracle_org(depth)
        if family == "path":
            return cls._oracle_path(depth)
        raise ValueError(f"unknown family {family!r}")

    @classmethod
    def _oracle_org(cls, depth: int) -> str:
        # CONNECT BY: Oracle-native hierarchical traversal. Single pass
        # over employees with hash-driven frontier expansion. ``LEVEL > 1``
        # excludes the anchor; ``LEVEL <= depth + 1`` gives N descendant
        # levels matching Mongo's ``maxDepth = depth - 1``.
        return f"""
SELECT COUNT(*) AS subordinate_count,
       ROUND(SUM(salary), 2) AS subtree_salary
FROM employees
WHERE LEVEL > 1
START WITH employee_id = {_ORG_ROOT}
CONNECT BY NOCYCLE PRIOR employee_id = manager_id
   AND LEVEL <= {depth + 1}
""".strip()

    @classmethod
    def _oracle_path(cls, depth: int) -> str:
        # CONNECT BY with native ``LEVEL`` pseudocolumn for one-pass
        # depth bucketing. ``LEVEL - 1`` re-bases to 1..depth so the
        # equivalence check aligns with Mongo's depthField+1.
        return f"""
SELECT (LEVEL - 1) AS lvl, COUNT(*) AS node_count
FROM employees
WHERE LEVEL > 1
START WITH employee_id = {_ORG_ROOT}
CONNECT BY NOCYCLE PRIOR employee_id = manager_id
   AND LEVEL <= {depth + 1}
GROUP BY (LEVEL - 1)
ORDER BY lvl
""".strip()

    # ── Predictions ──────────────────────────────────────────────────────

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(label="org-d5", parameters={"family": "org", "depth": 5})
        family = v.parameters["family"]
        depth = int(v.parameters["depth"])

        # The architectural claim: at full graph scale, Mongo's
        # ``$graphLookup`` (classic-engine BFS one level per iteration)
        # loses 15-20× to Oracle's CONNECT BY (CBO + parallel-eligible
        # hash-driven descendant walk). At small graph scale, both
        # engines complete in single-digit ms — a fair tie, not an
        # architectural verdict.
        if family == "org":
            # Tree saturates at depth ~7-8 with branching=5 (5^8 = 390K
            # vs 100K cap). Below saturation, both engines are fast.
            if depth <= 5:
                return [
                    Prediction(
                        claim=(
                            f"org-d{depth}: ratio in [0.3, 2.0] —"
                            " sub-saturation, both engines fast"
                        ),
                        metric="ratio_mongo_to_oracle",
                        operator="in",
                        expected_value=[0.3, 2.0],
                        confidence="medium-high",
                    ),
                ]
            return [
                Prediction(
                    claim=(
                        f"org-d{depth}: ratio ≥ 12.0 —"
                        " full-tree traversal exposes $graphLookup BFS cost"
                    ),
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=12.0,
                    confidence="high",
                ),
            ]
        # path
        if depth <= 5:
            return [
                Prediction(
                    claim=(
                        f"path-d{depth}: ratio in [0.5, 2.0] —"
                        " sub-saturation depth-bucketed enumeration"
                    ),
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[0.5, 2.0],
                    confidence="medium-high",
                ),
            ]
        return [
            Prediction(
                claim=(
                    f"path-d{depth}: ratio ≥ 12.0 —"
                    " CONNECT BY native LEVEL beats $graphLookup + $unwind + $group"
                ),
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=12.0,
                confidence="high",
            ),
        ]
