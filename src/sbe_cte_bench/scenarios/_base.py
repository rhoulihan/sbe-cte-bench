"""Scenario base class and registry.

Each scenario inherits from :class:`ScenarioBase` and declares:

- ``id`` / ``title``: identifiers matching ``docs/scenarios/Sxx-*.md``.
- ``mongo_pipeline()`` and ``oracle_sql()``: the executable workloads.
- ``predictions``: list of falsifiable claims with pass/fail thresholds.
- ``set_valued_paths``: array fields whose order shouldn't matter for
  equivalence (default: empty set; subclasses opt in for $addToSet results).
- ``sort_rows``: whether row order matters for equivalence (default True).
- ``variant_sweep``: parameters the scenario sweeps (e.g. boundary_position).

Concrete scenarios live in ``s01_baseline.py``, ``s02_sbe_prefix.py``, etc.
The registry pattern lets the CLI enumerate scenarios without hardcoded
imports of every module.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Prediction:
    """Falsifiable prediction with a pass/fail threshold."""

    claim: str
    metric: str  # e.g. "ratio", "median_ratio", "explain_has_classic_boundary"
    operator: str  # ">=", "<=", "in", "==", etc.
    expected_value: Any
    confidence: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class Variant:
    """One concrete configuration in a scenario's sweep."""

    label: str
    parameters: dict[str, Any]


class ScenarioBase(abc.ABC):
    """Abstract base for all benchmark scenarios.

    Subclasses are auto-registered when their module is imported.
    """

    # Subclass overrides — declarative.
    id: ClassVar[str] = ""  # e.g. "S01"
    title: ClassVar[str] = ""  # e.g. "Baseline scan + filter + project"
    set_valued_paths: ClassVar[frozenset[str]] = frozenset()
    sort_rows: ClassVar[bool] = True
    primary_collection: ClassVar[str] = "orders"

    @classmethod
    @abc.abstractmethod
    def mongo_pipeline(cls, variant: Variant | None = None) -> list[dict[str, Any]]:
        """Return the MongoDB aggregation pipeline for this scenario."""

    @classmethod
    @abc.abstractmethod
    def oracle_sql(cls, variant: Variant | None = None) -> str:
        """Return the Oracle SQL for this scenario."""

    @classmethod
    @abc.abstractmethod
    def predictions(cls, variant: Variant | None = None) -> list[Prediction]:
        """Return the falsifiable predictions for this variant."""

    @classmethod
    def variants(cls) -> list[Variant]:
        """Return the variant sweep. Default: a single nameless variant."""
        return [Variant(label="default", parameters={})]

    @classmethod
    def mongo_collection(cls, variant: Variant | None = None) -> str:
        """Collection the Mongo pipeline starts from.

        Default: ``primary_collection``. Scenarios with variant families
        that target different collections (e.g. S07's org/bom/cycle/path
        variants) override this.
        """
        return cls.primary_collection


# ─── Registry ─────────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[ScenarioBase]] = {}


def register(scenario_cls: type[ScenarioBase]) -> type[ScenarioBase]:
    """Decorator: register a concrete scenario class.

    Idempotent — re-registration of the same class is a no-op.
    """
    if not scenario_cls.id:
        raise ValueError(f"{scenario_cls.__name__} must declare an ``id``")
    existing = _REGISTRY.get(scenario_cls.id)
    if existing is not None and existing is not scenario_cls:
        raise ValueError(
            f"scenario id collision: {scenario_cls.id} already registered to {existing.__name__}"
        )
    _REGISTRY[scenario_cls.id] = scenario_cls
    return scenario_cls


def get_scenario(scenario_id: str) -> type[ScenarioBase]:
    """Look up a scenario class by id (e.g. ``"S01"``)."""
    if scenario_id not in _REGISTRY:
        raise KeyError(f"unknown scenario: {scenario_id}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[scenario_id]


def all_scenarios() -> list[type[ScenarioBase]]:
    """List all registered scenarios in id-sorted order."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


__all__ = (
    "Prediction",
    "ScenarioBase",
    "Variant",
    "all_scenarios",
    "get_scenario",
    "register",
)
