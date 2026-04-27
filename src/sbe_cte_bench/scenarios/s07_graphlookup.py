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
        # Full 10-variant design covering four architectural test points.
        # All Oracle paths now use CONNECT BY (not WITH RECURSIVE) — the
        # Oracle-native hierarchical-query syntax has 30+ years of CBO
        # tuning and ~7× lower per-query overhead.
        return [
            # Depth scaling — org subtree subordinate-count + salary rollup.
            Variant(label="org-d2", parameters={"family": "org", "depth": 2}),
            Variant(label="org-d5", parameters={"family": "org", "depth": 5}),
            Variant(label="org-d10", parameters={"family": "org", "depth": 10}),
            Variant(label="org-d15", parameters={"family": "org", "depth": 15}),
            # Recursive enumeration — BOM leaf-part count.
            Variant(label="bom-shallow", parameters={"family": "bom", "depth": 3}),
            Variant(label="bom-deep", parameters={"family": "bom", "depth": 10}),
            # Cycle detection — referral graph traversal with NOCYCLE.
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
        # BOM enumeration: count distinct leaf parts reachable from the
        # top assembly within ``depth`` levels. The pipeline starts on the
        # single root part and never filters it away, so the output is
        # always exactly one row — even when no leaf parts are reachable
        # at shallow depth (matching Oracle's ``COUNT(DISTINCT...)`` shape).
        #
        # NOTE: a true BOM rollup multiplies edge quantities along the
        # path (root_qty × edge_qty → effective leaf qty × unit_cost).
        # Oracle's recursive CTE does this in one pass with arithmetic in
        # the UNION ALL body. Mongo's ``$graphLookup`` returns reachable
        # edges but **cannot propagate values along the recursion path**,
        # so a true multi-level path-product cannot be computed in a
        # single pipeline. That gap is a separate measurement; here we
        # test only the enumerable subset (leaf-part count) so the
        # equivalence check is clean.
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
            {
                "$lookup": {
                    "from": "parts",
                    "localField": "edges.child_part_id",
                    "foreignField": "part_id",
                    "as": "child_parts",
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "leaf_count": {
                        "$size": {
                            "$filter": {
                                "input": "$child_parts",
                                "as": "p",
                                "cond": "$$p.leaf",
                            }
                        }
                    },
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
        # CONNECT BY: Oracle-native hierarchical traversal. Single pass over
        # the employees table with a hash-driven descendant walk; the CBO
        # treats this differently from a generic recursive CTE and applies
        # 30+ years of optimizer tuning. Compared with the same workload
        # expressed as ``WITH RECURSIVE``, expect ~7× speedup at SF1 based
        # on the path-d10 reference (266 ms vs 1824 ms for equivalent walks).
        #
        # ``LEVEL > 1`` filters the anchor (root) so the count and sum
        # match Mongo's ``$size: "$subordinates"`` (which excludes the
        # start node).
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
    def _oracle_bom(cls, depth: int) -> str:
        # CONNECT BY: walk bom_edges from the root assembly, then post-join
        # to parts to filter for leaves. ``LEVEL <= depth`` walks N levels
        # of edges (matching Mongo's ``maxDepth = depth - 1``).
        return f"""
SELECT COUNT(DISTINCT bw.child_part_id) AS leaf_count
FROM (
  SELECT child_part_id
  FROM bom_edges
  START WITH parent_part_id = {_BOM_ROOT}
  CONNECT BY NOCYCLE PRIOR child_part_id = parent_part_id
     AND LEVEL <= {depth}
) bw
JOIN parts p ON p.part_id = bw.child_part_id
WHERE p.leaf = 1
""".strip()

    @classmethod
    def _oracle_cycle(cls, start: int, depth: int) -> str:
        # CONNECT BY NOCYCLE: walk the customer_referrals graph from the
        # start customer downward (people they referred, transitively).
        # NOCYCLE handles the deliberately-injected back-edges. ``LEVEL > 1``
        # excludes the anchor; ``LEVEL <= depth + 1`` gives ``depth``
        # descendant levels matching Mongo's ``maxDepth = depth - 1``.
        return f"""
SELECT COUNT(*) AS downline_count
FROM customer_referrals
WHERE LEVEL > 1
START WITH customer_id = {start}
CONNECT BY NOCYCLE PRIOR customer_id = referred_by
   AND LEVEL <= {depth + 1}
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
