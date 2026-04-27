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
    is_autonomous: bool = False

    @property
    def ok(self) -> bool:
        # Autonomous DB doesn't expose SGA/PGA params to non-DBA users — the
        # OCPU envelope is enforced by Oracle Cloud, so missing values are
        # expected, not a failure.
        if self.is_autonomous:
            return self.server_version != "unknown"
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
            # Detect Autonomous DB — has CLOUD_SERVICE in v$pdbs / parameters
            # but more reliable signal is whether v$instance is queryable by
            # the current user (it's not on ADB).
            is_autonomous = False
            version = "unknown"
            try:
                cur.execute("SELECT version_full FROM v$instance")
                row = cur.fetchone()
                version = str(row[0]) if row else "unknown"
            except oracledb.DatabaseError:
                # ADB: v$instance is restricted. Fall back to v$version
                # (accessible to non-DBA users) and flag as autonomous.
                is_autonomous = True
                try:
                    cur.execute("SELECT banner_full FROM v$version WHERE rownum = 1")
                    row = cur.fetchone()
                    version = str(row[0]) if row else "unknown"
                except oracledb.DatabaseError:
                    cur.execute("SELECT banner FROM v$version WHERE rownum = 1")
                    row = cur.fetchone()
                    version = str(row[0]) if row else "unknown"

            sga_mb = 0
            pga_mb = 0
            try:
                cur.execute(
                    "SELECT NAME, VALUE FROM v$parameter"
                    " WHERE NAME IN ('sga_target', 'pga_aggregate_target')"
                )
                params = {name: int(value) for name, value in cur.fetchall()}
                sga_mb = params.get("sga_target", 0) // (1024 * 1024)
                pga_mb = params.get("pga_aggregate_target", 0) // (1024 * 1024)
            except oracledb.DatabaseError:
                # ADB: v$parameter restricted. Memory is OCPU-controlled.
                pass

            statspack = False
            try:
                cur.execute("SELECT COUNT(*) FROM dba_users WHERE username = 'PERFSTAT'")
                statspack_count = cur.fetchone()[0]
                statspack = bool(statspack_count)
            except oracledb.DatabaseError:
                # ADB: dba_users restricted; ADB has built-in AWR instead.
                pass

        return OraclePreflightStatus(
            server_version=version,
            sga_target_mb=sga_mb,
            pga_aggregate_target_mb=pga_mb,
            statspack_installed=statspack,
            is_autonomous=is_autonomous,
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

        Format ``'ALL'`` includes the access paths (so Exadata Smart Scan
        ``TABLE ACCESS STORAGE FULL`` is visible), parallel directives,
        and column projection.
        """
        with self.acquire() as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = 'sbecte' FOR {sql}")
            cur.execute(
                "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', 'sbecte', 'ALL'))"
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

    def execute_with_sql_monitor(
        self,
        sql: str,
        *,
        module: str = "sbe-cte-bench",
        action: str = "monitor",
    ) -> tuple[list[dict[str, Any]], str]:
        """Execute ``sql`` once with the ``MONITOR`` hint, then capture the
        active SQL Monitor HTML report for the executing session.

        Used for the post-measurement capture run. The query is timed
        separately (not folded into the iteration timings) and adds the
        ``/*+ MONITOR */`` hint to force SQL Monitor to record this
        execution even when the query finishes in <5 sec.

        On Autonomous Database the ``v$sql_monitor`` view is restricted to
        DBA users, so we identify the execution by the session's SID
        (visible to the connecting user via ``SYS_CONTEXT('USERENV','SID')``)
        and let ``DBMS_SQLTUNE.REPORT_SQL_MONITOR`` resolve the most-recent
        SQL_ID for that session internally.

        Returns ``(rows, html_active_report)``. The HTML is the raw active
        report (a self-contained HTML page including its CSS + JS hooks
        into Oracle's online diagnostic infrastructure).
        """
        sql_with_hint = self._inject_monitor_hint(sql)
        with self.acquire() as conn, conn.cursor() as cur:
            cur.callproc(
                "DBMS_APPLICATION_INFO.SET_MODULE", [module, action]
            )
            cur.execute("SELECT SYS_CONTEXT('USERENV', 'SID') FROM dual")
            sid = int(cur.fetchone()[0])

            cur.execute(sql_with_hint)
            columns = [d[0].lower() for d in (cur.description or [])]
            rows = [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]

            clob = cur.var(oracledb.DB_TYPE_CLOB)
            cur.execute(
                """
                BEGIN
                    :report := DBMS_SQLTUNE.REPORT_SQL_MONITOR(
                        session_id => :sid, type => 'ACTIVE'
                    );
                END;
                """,
                report=clob,
                sid=sid,
            )
            value = clob.getvalue()
            html = value.read() if value is not None else ""
            return rows, html

    @staticmethod
    def _inject_monitor_hint(sql: str) -> str:
        """Insert ``/*+ MONITOR */`` immediately after the leading SELECT/WITH.

        Robust to leading whitespace and the WITH-CTE prefix used by most
        recursive scenarios. Falls through to a no-op for non-SELECT
        statements (which shouldn't reach this path anyway).
        """
        lstripped = sql.lstrip()
        # ``WITH`` queries — hint goes after the first SELECT keyword inside.
        upper = lstripped.upper()
        if upper.startswith("SELECT "):
            return "SELECT /*+ MONITOR */ " + lstripped[len("SELECT "):]
        if upper.startswith("WITH "):
            # Find the first SELECT and inject after it.
            idx_select = upper.find("SELECT", len("WITH "))
            if idx_select >= 0:
                return (
                    lstripped[:idx_select]
                    + "SELECT /*+ MONITOR */"
                    + lstripped[idx_select + len("SELECT"):]
                )
        return sql

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
