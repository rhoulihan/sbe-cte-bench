"""S07 — recursive traversal: ``$graphLookup`` vs ``WITH RECURSIVE`` / ``CONNECT BY``.

Tests four architectural properties at SF1 scale:

1. **Depth scaling** (``org-d{N}``): how cost grows with traversal depth.
   Mongo ``$graphLookup`` is single-threaded BFS, one network round-trip per
   level. Oracle's recursive CTE can fuse / parallelize iterations.

2. **Recursive computation** (``bom-{shallow,deep}``): walking the tree is
   one thing; **propagating values** through it (BOM quantity rollup) is
   harder. Mongo has to ``$graphLookup`` then ``$unwind`` + ``$group`` to
   compute a path-product. Oracle does it in one CTE step with arithmetic
   in the recursive ``UNION ALL`` body.

3. **Cycle detection** (``cycle-{small,large}``): customer referral graph
   with deliberately injected back-edges. Mongo's ``$graphLookup`` prunes
   automatically; Oracle uses the recursive CTE ``CYCLE`` clause /
   ``CONNECT_BY_NOCYCLE``.

4. **Path materialization** (``path-d{N}``): not just enumeration — building
   the actual root-to-node path. Oracle has ``SYS_CONNECT_BY_PATH``; Mongo
   needs ``$reduce`` over the descendants array.

All variants run against entities sized to make traversal a real workload:
employees=100K (depth ~7-8 with branching=5), parts=50K (~6 BOM levels),
customer_referrals=100K with ~500 cycles.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


# Starting points chosen to give each variant a meaningfully-sized subtree.
# employee_id=1 is the deterministic root of the org tree (CEO).
# part_id=1 is the deterministic root of the BOM (top assembly).
# customer_id=1 is the implicit referral root.
_ORG_ROOT = 1
_BOM_ROOT = 1
_CYCLE_ROOT_SMALL = 100  # outside the cycle injection range
_CYCLE_ROOT_LARGE = 1  # near the head of the chain — likely to hit cycles


@register
class S07Recursive(ScenarioBase):
    id: ClassVar[str] = "S07"
    title: ClassVar[str] = "Recursive traversal: $graphLookup vs WITH RECURSIVE / CONNECT BY"
    primary_collection: ClassVar[str] = "employees"

    @classmethod
    def mongo_collection(cls, variant: Variant | None = None) -> str:
        """org/path → ``employees``; bom → ``parts``; cycle → ``customers``."""
        if variant is None:
            return cls.primary_collection
        family = variant.parameters.get("family")
        if family == "bom":
            return "parts"
        if family == "cycle":
            return "customers"
        return "employees"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            # Depth scaling — org subtree subordinate-count + salary rollup.
            Variant(label="org-d2", parameters={"family": "org", "depth": 2}),
            Variant(label="org-d5", parameters={"family": "org", "depth": 5}),
            Variant(label="org-d10", parameters={"family": "org", "depth": 10}),
            Variant(label="org-d15", parameters={"family": "org", "depth": 15}),
            # Recursive computation — BOM quantity rollup.
            Variant(label="bom-shallow", parameters={"family": "bom", "depth": 3}),
            Variant(label="bom-deep", parameters={"family": "bom", "depth": 10}),
            # Cycle detection — referral graph traversal.
            Variant(
                label="cycle-small",
                parameters={"family": "cycle", "start": _CYCLE_ROOT_SMALL, "depth": 10},
            ),
            Variant(
                label="cycle-large",
                parameters={"family": "cycle", "start": _CYCLE_ROOT_LARGE, "depth": 20},
            ),
            # Path materialization.
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
        if family == "bom":
            return cls._mongo_bom(depth)
        if family == "cycle":
            return cls._mongo_cycle(int(v.parameters["start"]), depth)
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
                    "maxDepth": depth - 1,  # maxDepth is 0-indexed
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
    def _mongo_bom(cls, depth: int) -> list[dict[str, Any]]:
        # BOM rollup is harder for Mongo: $graphLookup gets all reachable
        # edges, then we $unwind them and join to leaf parts to compute a
        # rolled-up quantity-weighted leaf-cost sum. The "depth" parameter
        # controls maxDepth — bom-shallow stops at level 3, bom-deep walks
        # full graph.
        return [
            {"$match": {"part_id": _BOM_ROOT}},
            {
                "$graphLookup": {
                    "from": "bom_edges",
                    "startWith": "$part_id",
                    "connectFromField": "child_part_id",
                    "connectToField": "parent_part_id",
                    "maxDepth": depth - 1,
                    "as": "edges",
                }
            },
            {"$unwind": "$edges"},
            {
                "$lookup": {
                    "from": "parts",
                    "localField": "edges.child_part_id",
                    "foreignField": "part_id",
                    "as": "child_part",
                }
            },
            {"$unwind": "$child_part"},
            {"$match": {"child_part.leaf": True}},
            {
                "$group": {
                    "_id": None,
                    "leaf_count": {"$sum": 1},
                    "total_cost": {
                        "$sum": {
                            "$multiply": ["$edges.quantity", "$child_part.unit_cost"]
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "leaf_count": 1,
                    "total_cost": {"$round": ["$total_cost", 2]},
                }
            },
        ]

    @classmethod
    def _mongo_cycle(cls, start: int, depth: int) -> list[dict[str, Any]]:
        # Referrals traversal with maxDepth — Mongo's $graphLookup
        # auto-prunes cycles by tracking visited connectFromField values.
        return [
            {"$match": {"customer_id": start}},
            {
                "$graphLookup": {
                    "from": "customers",
                    "startWith": "$customer_id",
                    "connectFromField": "customer_id",
                    "connectToField": "referred_by",
                    "maxDepth": depth - 1,
                    "as": "downline",
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "downline_count": {"$size": "$downline"},
                }
            },
        ]

    @classmethod
    def _mongo_path(cls, depth: int) -> list[dict[str, Any]]:
        # Path materialization: count nodes at each depth level under the
        # root, up to ``depth`` levels. Mongo's ``depthField`` is 0-indexed
        # for direct children of the start node, so for ``depth=N`` we
        # walk levels 0..(N-1) (N levels total). We add 1 to align with
        # Oracle's 1-indexed LEVEL semantics in the equivalence check.
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
        if family == "bom":
            return cls._oracle_bom(depth)
        if family == "cycle":
            return cls._oracle_cycle(int(v.parameters["start"]), depth)
        if family == "path":
            return cls._oracle_path(depth)
        raise ValueError(f"unknown family {family!r}")

    @classmethod
    def _oracle_org(cls, depth: int) -> str:
        # Recursive CTE — count subordinates within depth levels and sum
        # their salaries. ``LEVEL <= depth + 1`` because the anchor row is
        # level 1 and the recursion adds one level per step. We exclude the
        # root from the aggregate so output matches Mongo's
        # ``$size: "$subordinates"`` (which doesn't include the start node).
        return f"""
WITH org_tree (employee_id, manager_id, salary, lvl) AS (
  SELECT employee_id, manager_id, salary, 1
  FROM employees
  WHERE employee_id = {_ORG_ROOT}
  UNION ALL
  SELECT e.employee_id, e.manager_id, e.salary, ot.lvl + 1
  FROM employees e
  JOIN org_tree ot ON e.manager_id = ot.employee_id
  WHERE ot.lvl < {depth + 1}
)
SELECT
  (SELECT COUNT(*) FROM org_tree WHERE lvl > 1) AS subordinate_count,
  (SELECT ROUND(SUM(salary), 2) FROM org_tree WHERE lvl > 1) AS subtree_salary
FROM dual
""".strip()

    @classmethod
    def _oracle_bom(cls, depth: int) -> str:
        # Recursive CTE — propagate effective quantity through the BOM.
        # Each recursive step multiplies parent quantity by child quantity
        # (path-product). At leaves, multiply by unit_cost.
        return f"""
WITH bom_walk (root_part_id, current_part_id, effective_qty, lvl) AS (
  SELECT part_id, part_id, 1, 0
  FROM parts
  WHERE part_id = {_BOM_ROOT}
  UNION ALL
  SELECT bw.root_part_id, e.child_part_id,
         bw.effective_qty * e.quantity, bw.lvl + 1
  FROM bom_walk bw
  JOIN bom_edges e ON e.parent_part_id = bw.current_part_id
  WHERE bw.lvl < {depth}
)
SELECT
  COUNT(*) AS leaf_count,
  ROUND(SUM(bw.effective_qty * p.unit_cost), 2) AS total_cost
FROM bom_walk bw
JOIN parts p ON p.part_id = bw.current_part_id
WHERE p.leaf = 1
""".strip()

    @classmethod
    def _oracle_cycle(cls, start: int, depth: int) -> str:
        # Recursive CTE with the CYCLE clause — Oracle 11g+ deduplicates
        # paths that revisit the same row. ``cycle_mark`` is a generated
        # column flagging cyclic paths.
        return f"""
WITH downline (customer_id, referred_by, lvl) AS (
  SELECT customer_id, referred_by, 0
  FROM customer_referrals
  WHERE customer_id = {start}
  UNION ALL
  SELECT cr.customer_id, cr.referred_by, d.lvl + 1
  FROM downline d
  JOIN customer_referrals cr ON cr.referred_by = d.customer_id
  WHERE d.lvl < {depth - 1}
) CYCLE customer_id SET cycle_mark TO 'Y' DEFAULT 'N'
SELECT COUNT(*) - 1 AS downline_count
FROM downline
""".strip()

    @classmethod
    def _oracle_path(cls, depth: int) -> str:
        # CONNECT BY: Oracle's native hierarchical-query syntax. ``LEVEL``
        # is 1-indexed at the anchor, so ``LEVEL <= depth + 1`` walks N
        # levels of descendants. We filter ``LEVEL > 1`` to drop the anchor
        # itself, matching Mongo's ``depthField`` which doesn't include the
        # start node. ``LEVEL - 1`` re-bases to 1..depth for the equivalence
        # check.
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

        # Architectural claim: Mongo's $graphLookup is classic-only and
        # single-threaded BFS one level per round-trip. At realistic depth
        # (≥5) the per-iteration round-trip cost compounds. Oracle's
        # recursive CTE / CONNECT BY can fuse iterations and run in
        # parallel under the CBO.
        if family == "org":
            # Depth-scaling: ratio should rise with depth.
            target = {2: 1.0, 5: 2.0, 10: 4.0, 15: 6.0}.get(depth, 3.0)
            return [
                Prediction(
                    claim=(
                        f"org-d{depth}: ratio_mongo_to_oracle ≥ {target} —"
                        " $graphLookup BFS round-trips compound with depth"
                    ),
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=target,
                    confidence="medium",
                ),
            ]
        if family == "bom":
            # Recursive computation — Oracle's CTE arithmetic in UNION ALL
            # is fused; Mongo needs $unwind + $group post-traversal.
            target = 3.0 if depth <= 5 else 5.0
            return [
                Prediction(
                    claim=(
                        f"bom-{'shallow' if depth <= 5 else 'deep'}:"
                        f" ratio ≥ {target} — recursive arithmetic favors fused CTE"
                    ),
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=target,
                    confidence="medium",
                ),
            ]
        if family == "cycle":
            # Cycle detection — both engines handle it; expect rough parity
            # to mild Oracle advantage thanks to CTE fusion.
            return [
                Prediction(
                    claim=(
                        f"cycle: ratio_mongo_to_oracle in [0.5, 5.0] —"
                        " both engines prune cycles correctly"
                    ),
                    metric="ratio_mongo_to_oracle",
                    operator="in",
                    expected_value=[0.5, 5.0],
                    confidence="medium",
                ),
            ]
        # path
        target = 2.0 if depth <= 5 else 4.0
        return [
            Prediction(
                claim=(
                    f"path-d{depth}: ratio ≥ {target} —"
                    " CONNECT BY's native depth tracking beats $graphLookup + $group"
                ),
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=target,
                confidence="medium",
            ),
        ]
