"""S07 — $graphLookup vs recursive CTE. Per docs/scenarios/S07-graphlookup-recursive.md."""

from __future__ import annotations

from typing import Any, ClassVar

from sbe_cte_bench.scenarios._base import Prediction, ScenarioBase, Variant, register


@register
class S07GraphLookup(ScenarioBase):
    id: ClassVar[str] = "S07"
    title: ClassVar[str] = "Recursive traversal: $graphLookup vs recursive CTE"
    primary_collection: ClassVar[str] = "categories"

    @classmethod
    def variants(cls) -> list[Variant]:
        return [
            Variant(label=f"unsharded-d{d}", parameters={"depth": d, "topology": "standard"})
            for d in (4, 5, 6, 8)
        ] + [
            Variant(label=f"sharded-d{d}", parameters={"depth": d, "topology": "sharded"})
            for d in (4, 5, 6)
        ]

    @classmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        v = variant or Variant(
            label="unsharded-d6", parameters={"depth": 6, "topology": "standard"}
        )
        depth = int(v.parameters["depth"])
        return [
            {"$match": {"parent_id": None}},
            {
                "$graphLookup": {
                    "from": "categories",
                    "startWith": "$category_id",
                    "connectFromField": "category_id",
                    "connectToField": "parent_id",
                    "as": "descendants",
                    "maxDepth": depth
                    - 1,  # descendants at depths 1..depth+1 = 1..N+1; subtract 1 to align with Oracle
                    "depthField": "depth",
                }
            },
            {"$unwind": "$descendants"},
            {
                "$lookup": {
                    "from": "products",
                    "localField": "descendants.category_id",
                    "foreignField": "category_id",
                    "as": "products",
                }
            },
            {
                "$group": {
                    "_id": {
                        "root_id": "$category_id",
                        "category_id": "$descendants.category_id",
                    },
                    "depth": {"$first": {"$add": ["$descendants.depth", 1]}},
                    "product_count": {"$first": {"$size": "$products"}},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "root_id": "$_id.root_id",
                    "category_id": "$_id.category_id",
                    "depth": 1,
                    "product_count": 1,
                }
            },
        ]

    @classmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        v = variant or Variant(
            label="unsharded-d6", parameters={"depth": 6, "topology": "standard"}
        )
        depth = int(v.parameters["depth"])
        return f"""
WITH category_tree (root_id, category_id, depth) AS (
  SELECT category_id AS root_id, category_id, 0 AS depth
  FROM categories
  WHERE parent_id IS NULL
  UNION ALL
  SELECT t.root_id, c.category_id, t.depth + 1
  FROM category_tree t
  JOIN categories c ON c.parent_id = t.category_id
  WHERE t.depth < {depth}
)
SELECT ct.root_id, ct.category_id, ct.depth, COUNT(p.product_id) AS product_count
FROM category_tree ct
LEFT JOIN products p ON p.category_id = ct.category_id
WHERE ct.depth > 0  -- match Mongo $graphLookup's descendants-only semantics
GROUP BY ct.root_id, ct.category_id, ct.depth
ORDER BY ct.root_id, ct.depth, ct.category_id
""".strip()

    @classmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        v = variant or Variant(
            label="unsharded-d6", parameters={"depth": 6, "topology": "standard"}
        )
        depth = int(v.parameters["depth"])
        topology = v.parameters["topology"]
        if topology == "sharded":
            return [
                Prediction(
                    claim=f"sharded depth={depth}: ratio >= 25x (classic + scatter-gather compounding)",
                    metric="ratio_mongo_to_oracle",
                    operator=">=",
                    expected_value=25.0 if depth >= 5 else 15.0,
                    confidence="very high",
                ),
            ]
        # unsharded
        return [
            Prediction(
                claim=f"depth={depth}: $graphLookup ratio rises with depth",
                metric="ratio_mongo_to_oracle",
                operator=">=",
                expected_value=5.0 if depth <= 4 else 8.0 if depth <= 6 else 15.0,
                confidence="medium-high",
            ),
            Prediction(
                claim="explain shows $cursor boundary right after $match",
                metric="mongo_classic_boundary_at_stage",
                operator="==",
                expected_value=1,
                confidence="very high",
            ),
        ]
