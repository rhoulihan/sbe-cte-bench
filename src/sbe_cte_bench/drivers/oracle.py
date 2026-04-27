"""Thin wrapper around ``python-oracledb`` thin mode for the benchmark.

Hooks into:

- ``dbms_xplan.display_cursor`` for plan capture.
- ``v$sql_workarea_active`` / ``v$pgastat`` for workarea instrumentation.
- ``STATSPACK.SNAP`` for system-wide snapshot pairs.
- Plan-cache and shared-pool flushes between scenarios.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import oracledb


@dataclass(frozen=True)
class OraclePreflightStatus:
    server_version: str
    sga_target_mb: int
    pga_aggregate_target_mb: int
    statspack_installed: bool

    @property
    def ok(self) -> bool:
        return self.sga_target_mb > 0 and self.pga_aggregate_target_mb > 0


class OracleBench:
    """Connection wrapper for the BENCH user.

    Supports both Oracle Free (host/service-name DSN) and Autonomous DB
    (wallet + TNS alias). For ADB, set ``config_dir`` to the unzipped
    wallet directory and ``dsn`` to a TNS alias (e.g. ``rhbench_high``).
    """

    def __init__(
        self,
        *,
        user: str = "BENCH",
        password: str = "BenchPass2026",
        dsn: str = "localhost/FREEPDB1",
        config_dir: str | None = None,
        wallet_location: str | None = None,
        wallet_password: str | None = None,
    ) -> None:
        pool_kwargs: dict[str, Any] = {
            "user": user,
            "password": password,
            "dsn": dsn,
            "min": 1,
            "max": 4,
            "increment": 1,
            "getmode": oracledb.POOL_GETMODE_WAIT,
        }
        if config_dir is not None:
            # Wallet-based connection (Autonomous DB). thin-mode python-oracledb
            # picks up tnsnames.ora + cwallet.sso from this directory.
            pool_kwargs["config_dir"] = config_dir
            pool_kwargs["wallet_location"] = wallet_location or config_dir
            if wallet_password is not None:
                pool_kwargs["wallet_password"] = wallet_password
        self._pool = oracledb.create_pool(**pool_kwargs)

    @contextmanager
    def acquire(self) -> Iterator[oracledb.Connection]:
        """Acquire a connection from the pool. Releases on exit."""
        conn = self._pool.acquire()
        try:
            yield conn
        finally:
            self._pool.release(conn)

    def preflight(self) -> OraclePreflightStatus:
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute("SELECT version_full FROM v$instance")
            row = cur.fetchone()
            version = str(row[0]) if row else "unknown"

            cur.execute(
                "SELECT NAME, VALUE FROM v$parameter WHERE NAME IN ('sga_target', 'pga_aggregate_target')"
            )
            params = {name: int(value) for name, value in cur.fetchall()}
            sga_mb = params.get("sga_target", 0) // (1024 * 1024)
            pga_mb = params.get("pga_aggregate_target", 0) // (1024 * 1024)

            cur.execute("SELECT COUNT(*) FROM dba_users WHERE username = 'PERFSTAT'")
            statspack_count = cur.fetchone()[0]
            statspack = bool(statspack_count)

        return OraclePreflightStatus(
            server_version=version,
            sga_target_mb=sga_mb,
            pga_aggregate_target_mb=pga_mb,
            statspack_installed=statspack,
        )

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return rows as a list of dicts."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute(sql, parameters or {})
            columns = [d[0].lower() for d in (cur.description or [])]
            return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]

    def stream(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream rows lazily — used when the result set may not fit in RAM."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute(sql, parameters or {})
            columns = [d[0].lower() for d in (cur.description or [])]
            while True:
                rows = cur.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    yield dict(zip(columns, row, strict=False))

    def explain_plan(self, sql: str) -> str:
        """Capture a dbms_xplan.display text plan for the given SQL.

        Uses ``EXPLAIN PLAN FOR`` + ``DBMS_XPLAN.DISPLAY`` to get a stable
        text representation. Doesn't execute the query.
        """
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = 'sbecte' FOR {sql}")
            cur.execute(
                "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'sbecte', "
                "'TYPED ROWS BYTES COST PARTITION PARALLEL PREDICATE PROJECTION'))"
            )
            return "\n".join(row[0] for row in cur.fetchall() if row[0] is not None)

    def display_cursor(self, sql_id: str) -> str:
        """Capture an actual-execution dbms_xplan for a recently-executed SQL."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:sql_id, NULL, 'ALLSTATS LAST'))",
                {"sql_id": sql_id},
            )
            return "\n".join(row[0] for row in cur.fetchall() if row[0] is not None)

    def flush_shared_pool(self) -> None:
        """Clear the shared pool between scenarios."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute("ALTER SYSTEM FLUSH SHARED_POOL")

    def flush_buffer_cache(self) -> None:
        """Clear the buffer cache for cold-cache runs."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute("ALTER SYSTEM FLUSH BUFFER_CACHE")

    def gather_table_stats(self, owner: str, table: str) -> None:
        """Gather statistics on a table. Called after data load."""
        with self.acquire() as conn, conn.cursor() as cur:
            cur.callproc(
                "DBMS_STATS.GATHER_TABLE_STATS",
                [
                    owner,
                    table,
                    None,
                    None,
                    True,
                    "FOR ALL COLUMNS SIZE AUTO",
                    None,
                    None,
                    "ALL",
                    True,
                ],
            )

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> OracleBench:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@contextmanager
def open_oracle(
    *,
    user: str = "BENCH",
    password: str = "BenchPass2026",
    dsn: str = "localhost/FREEPDB1",
    config_dir: str | None = None,
    wallet_location: str | None = None,
    wallet_password: str | None = None,
) -> Iterator[OracleBench]:
    bench = OracleBench(
        user=user,
        password=password,
        dsn=dsn,
        config_dir=config_dir,
        wallet_location=wallet_location,
        wallet_password=wallet_password,
    )
    try:
        yield bench
    finally:
        bench.close()


__all__ = ("OracleBench", "OraclePreflightStatus", "open_oracle")
