"""Scenario registry — importing this package registers all scenarios."""

from __future__ import annotations

# Import each scenario module so its @register decorator runs and adds it to
# the registry. Order is irrelevant; the registry is dict-keyed by id.
from sbe_cte_bench.scenarios import (
    s01_baseline,
    s02_sbe_prefix,
    s03_boundary_tax,
    s04_stage_wall,
    s05_doc_cap,
    s06_lookup_sharded,
    s07_graphlookup,
    s08_window_functions,
    s09_predicate_pushdown,
    s10_top_n,
    s12_concurrent,
    s13_planner_stability,
    s14_write_path,
    s15_plan_cache,
)
from sbe_cte_bench.scenarios._base import (
    Prediction,
    ScenarioBase,
    Variant,
    all_scenarios,
    get_scenario,
)

__all__ = (
    "Prediction",
    "ScenarioBase",
    "Variant",
    "all_scenarios",
    "get_scenario",
    "s01_baseline",
    "s02_sbe_prefix",
    "s03_boundary_tax",
    "s04_stage_wall",
    "s05_doc_cap",
    "s06_lookup_sharded",
    "s07_graphlookup",
    "s08_window_functions",
    "s09_predicate_pushdown",
    "s10_top_n",
    "s12_concurrent",
    "s13_planner_stability",
    "s14_write_path",
    "s15_plan_cache",
)
