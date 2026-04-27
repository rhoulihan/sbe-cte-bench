"""Tests for the driver wrappers.

These are *unit* tests: imports succeed, module-level types resolve, smoke
verification of dataclass shapes. Live driver behavior is covered by the
``integration`` test suite (gated behind testcontainers).
"""

from __future__ import annotations

import pytest

from sbe_cte_bench.drivers.mongo import MongoPreflightStatus
from sbe_cte_bench.drivers.oracle import OraclePreflightStatus


@pytest.mark.unit
def test_mongo_preflight_ok_when_all_conditions_met() -> None:
    status = MongoPreflightStatus(
        framework_control="trySbeEngine",
        journal_enabled=True,
        replica_set_initialized=True,
        server_version="8.2.2",
    )
    assert status.ok is True


@pytest.mark.unit
def test_mongo_preflight_not_ok_when_classic_engine_forced() -> None:
    status = MongoPreflightStatus(
        framework_control="forceClassicEngine",
        journal_enabled=True,
        replica_set_initialized=True,
        server_version="8.2.2",
    )
    assert status.ok is False


@pytest.mark.unit
def test_mongo_preflight_not_ok_without_journal() -> None:
    status = MongoPreflightStatus(
        framework_control="trySbeEngine",
        journal_enabled=False,
        replica_set_initialized=True,
        server_version="8.2.2",
    )
    assert status.ok is False


@pytest.mark.unit
def test_mongo_preflight_not_ok_when_replica_set_not_initialized() -> None:
    status = MongoPreflightStatus(
        framework_control="trySbeEngine",
        journal_enabled=True,
        replica_set_initialized=False,
        server_version="8.2.2",
    )
    assert status.ok is False


@pytest.mark.unit
def test_oracle_preflight_ok_when_targets_set() -> None:
    status = OraclePreflightStatus(
        server_version="26.0.0.0",
        sga_target_mb=1200,
        pga_aggregate_target_mb=600,
        statspack_installed=True,
    )
    assert status.ok is True


@pytest.mark.unit
def test_oracle_preflight_not_ok_when_targets_unset() -> None:
    status = OraclePreflightStatus(
        server_version="26.0.0.0",
        sga_target_mb=0,
        pga_aggregate_target_mb=0,
        statspack_installed=False,
    )
    assert status.ok is False
