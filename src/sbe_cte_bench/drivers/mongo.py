"""Thin wrapper around ``pymongo`` for the benchmark harness.

Pre-flight verification:

- ``internalQueryFrameworkControl`` must be ``trySbeEngine`` (the SBE-on
  default in 8.0+). If it's been flipped to classic, the run is invalid.
- Journal must be enabled (always-on in 8.x; we verify defensively).
- Replica set must be initiated (single-node ``bench`` rs.initiate'd).

The class is purely orchestration: takes a connection string, exposes the
collection-level operations the runner needs, and provides hooks for
pre-iteration cache clearing and post-iteration spill capture.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pymongo
from pymongo import MongoClient


@dataclass(frozen=True)
class MongoPreflightStatus:
    framework_control: str
    journal_enabled: bool
    replica_set_initialized: bool
    server_version: str

    @property
    def ok(self) -> bool:
        return (
            self.framework_control == "trySbeEngine"
            and self.journal_enabled
            and self.replica_set_initialized
        )


class MongoBench:
    """Connection wrapper bound to a single benchmark database."""

    def __init__(self, *, uri: str, database: str = "bench") -> None:
        self._client: MongoClient[dict[str, Any]] = MongoClient(
            uri, directConnection=False, serverSelectionTimeoutMS=10_000
        )
        self._db_name = database

    @property
    def client(self) -> MongoClient[dict[str, Any]]:
        return self._client

    @property
    def db(self) -> Any:
        return self._client[self._db_name]

    def preflight(self) -> MongoPreflightStatus:
        """Verify the engine is in the expected configuration before timing.

        A scenario that runs against a misconfigured mongod produces
        meaningless numbers — we'd be measuring classic engine while
        believing we measured SBE. Fail fast and loudly.
        """
        admin = self._client.admin
        framework = admin.command({"getParameter": 1, "internalQueryFrameworkControl": 1})
        framework_control = str(framework.get("internalQueryFrameworkControl", "unknown"))

        status = admin.command({"serverStatus": 1})
        wt_log = (status.get("wiredTiger") or {}).get("log") or {}
        journal_enabled = bool(wt_log)
        version = str(status.get("version", "unknown"))

        try:
            rs_status = admin.command({"replSetGetStatus": 1})
            rs_initialized = rs_status.get("ok") == 1
        except pymongo.errors.OperationFailure:
            rs_initialized = False

        return MongoPreflightStatus(
            framework_control=framework_control,
            journal_enabled=journal_enabled,
            replica_set_initialized=rs_initialized,
            server_version=version,
        )

    def aggregate(
        self,
        collection: str,
        pipeline: list[dict[str, Any]],
        *,
        allow_disk_use: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Run an aggregation, returning a cursor of result documents."""
        cursor = self.db[collection].aggregate(pipeline, allowDiskUse=allow_disk_use)
        yield from cursor

    def explain(
        self,
        collection: str,
        pipeline: list[dict[str, Any]],
        *,
        verbosity: str = "executionStats",
    ) -> dict[str, Any]:
        """Capture an explain plan for the given pipeline."""
        result = self.db.command(
            {
                "explain": {"aggregate": collection, "pipeline": pipeline, "cursor": {}},
                "verbosity": verbosity,
            }
        )
        return dict(result)

    def clear_plan_cache(self, collection: str) -> None:
        """Clear the per-collection plan cache between scenarios."""
        # Collection may not exist or planCacheClear may be unavailable;
        # suppress so test setup is resilient.
        with contextlib.suppress(pymongo.errors.OperationFailure):
            self.db[collection].database.command({"planCacheClear": collection})

    def get_recent_profile_entries(
        self, since_ts: Any, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Pull the last N entries from system.profile after a timestamp."""
        coll = self.db["system.profile"]
        return list(coll.find({"ts": {"$gte": since_ts}}).sort("ts", 1).limit(limit))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MongoBench:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@contextmanager
def open_mongo(
    uri: str = "mongodb://localhost:27017", database: str = "bench"
) -> Iterator[MongoBench]:
    """Context-manager helper for callers that don't want to manage lifetime."""
    bench = MongoBench(uri=uri, database=database)
    try:
        yield bench
    finally:
        bench.close()


__all__ = ("MongoBench", "MongoPreflightStatus", "open_mongo")
