"""CLI entry point — wires every subsystem together.

Subcommands:

- ``infra up|down|verify`` — bring topologies up/down and verify resource limits.
- ``data generate`` — emit deterministic JSONL data for a scale factor.
- ``data load`` — push generated data into both engines.
- ``run <scenario_id>`` — execute a scenario and produce a run record.
- ``report aggregate`` — collapse raw run records into the summary CSV.
- ``report scenario <id>`` — produce the per-scenario markdown writeup.
- ``list scenarios`` — enumerate the registered scenarios with their variants.
- ``--version`` — print package version.

The CLI is the user-facing surface for the benchmark. Per
``IMPLEMENTATION-PLAN.md``, "a user who runs ``sbe-cte-bench`` without flags
gets a defensible result" — that's the contract this module enforces.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from sbe_cte_bench import __version__
from sbe_cte_bench.data.generator import ScaleFactor, generate
from sbe_cte_bench.scenarios import all_scenarios, get_scenario


@click.group(name="sbe-cte-bench", help="Benchmark MongoDB SBE vs Oracle nested CTEs.")
@click.version_option(__version__, prog_name="sbe-cte-bench")
def main_group() -> None:
    """Top-level command group."""


# ─── data ───────────────────────────────────────────────────────────────


@main_group.group(name="data", help="Data generation + loading commands.")
def data_group() -> None:
    pass


@data_group.command("generate")
@click.option(
    "--scale",
    type=click.Choice([s.value for s in ScaleFactor]),
    default=ScaleFactor.SF0_001.value,
    help="Scale factor.",
)
@click.option("--seed", type=int, default=0xCAFE, help="PRNG seed.")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("data/generated"),
    help="Directory to write JSONL files into.",
)
def data_generate(scale: str, seed: int, output_dir: Path) -> None:
    """Generate the benchmark dataset."""
    sf = ScaleFactor(scale)
    manifest = generate(scale=sf, output_dir=output_dir, seed=seed)
    click.echo(f"generated {len(manifest.hashes)} files in {output_dir}")
    for name, h in manifest.hashes.items():
        click.echo(
            f"  {name}  {h[:12]}...  ({manifest.counts.get(name.replace('.jsonl', ''), '?')} rows)"
        )


@data_group.command("load")
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("data/generated"),
)
@click.option("--mongo-uri", default="mongodb://localhost:27017")
@click.option("--mongo-db", default="bench")
@click.option("--oracle-dsn", default=lambda: os.environ.get("ORACLE_DSN", "localhost/FREEPDB1"))
@click.option("--oracle-user", default=lambda: os.environ.get("ORACLE_USER", "BENCH"))
@click.option("--oracle-password", default=lambda: os.environ.get("ORACLE_PASSWORD", "BenchPass2026"))
@click.option(
    "--oracle-config-dir",
    default=lambda: os.environ.get("ORACLE_CONFIG_DIR"),
    help="Wallet directory (tnsnames.ora + cwallet.sso); enables ADB connections.",
)
@click.option(
    "--oracle-wallet-password",
    default=lambda: os.environ.get("ORACLE_WALLET_PASSWORD"),
    help="Wallet password if the wallet is not auto-login (cwallet.sso).",
)
@click.option("--target", type=click.Choice(["both", "mongo", "oracle"]), default="both")
def data_load(
    data_dir: Path,
    mongo_uri: str,
    mongo_db: str,
    oracle_dsn: str,
    oracle_user: str,
    oracle_password: str,
    oracle_config_dir: str | None,
    oracle_wallet_password: str | None,
    target: str,
) -> None:
    """Load generated data into the engines."""
    if target in ("both", "mongo"):
        from sbe_cte_bench.data.load_mongo import load_mongodb
        from sbe_cte_bench.drivers.mongo import open_mongo

        with open_mongo(uri=mongo_uri, database=mongo_db) as bench_m:
            mongo_stats = load_mongodb(bench=bench_m, data_dir=data_dir)
            click.echo("mongo load stats:")
            for coll, m_s in mongo_stats.items():
                click.echo(f"  {coll}: {m_s.inserted} rows in {m_s.elapsed_s:.2f}s")

    if target in ("both", "oracle"):
        from sbe_cte_bench.data.load_oracle import create_schema, load_oracle
        from sbe_cte_bench.drivers.oracle import open_oracle

        with open_oracle(
            user=oracle_user,
            password=oracle_password,
            dsn=oracle_dsn,
            config_dir=oracle_config_dir,
            wallet_password=oracle_wallet_password,
        ) as bench_o:
            create_schema(bench_o)
            oracle_stats = load_oracle(bench=bench_o, data_dir=data_dir)
            click.echo("oracle load stats:")
            for tbl, o_s in oracle_stats.items():
                click.echo(f"  {tbl}: {o_s.inserted} rows in {o_s.elapsed_s:.2f}s")


# ─── infra ──────────────────────────────────────────────────────────────
#
# The harness expects a user-provided test environment:
#   • Oracle Autonomous Database (Always Free is sufficient) with a
#     wallet downloaded to disk and exposed via ORACLE_CONFIG_DIR.
#   • A native MongoDB instance (typically on a separate VM) capped at
#     the same OCPU / memory envelope as the ADB tier — see
#     ``infra/install-mongodb-cgroup-capped.sh`` for an opinionated
#     systemd cgroup setup that matches Always Free (1 OCPU / 3 GB).
#
# ``infra verify`` is the only lifecycle command — it preflights both
# engines and reports whether they're reachable and properly configured.


@main_group.group(name="infra", help="Topology preflight commands.")
def infra_group() -> None:
    pass


@infra_group.command("verify")
@click.option("--mongo-uri", default="mongodb://localhost:27017")
@click.option("--oracle-dsn", default=lambda: os.environ.get("ORACLE_DSN", "localhost/FREEPDB1"))
@click.option("--oracle-user", default=lambda: os.environ.get("ORACLE_USER", "BENCH"))
@click.option("--oracle-password", default=lambda: os.environ.get("ORACLE_PASSWORD", "BenchPass2026"))
@click.option(
    "--oracle-config-dir",
    default=lambda: os.environ.get("ORACLE_CONFIG_DIR"),
    help="Wallet directory (tnsnames.ora + cwallet.sso); enables ADB connections.",
)
@click.option(
    "--oracle-wallet-password",
    default=lambda: os.environ.get("ORACLE_WALLET_PASSWORD"),
    help="Wallet password if the wallet is not auto-login (cwallet.sso).",
)
def infra_verify(
    mongo_uri: str,
    oracle_dsn: str,
    oracle_user: str,
    oracle_password: str,
    oracle_config_dir: str | None,
    oracle_wallet_password: str | None,
) -> None:
    """Pre-flight verification of both engines."""
    from sbe_cte_bench.drivers.mongo import open_mongo
    from sbe_cte_bench.drivers.oracle import open_oracle

    failed = False

    try:
        with open_mongo(uri=mongo_uri) as bench:
            status = bench.preflight()
            click.echo(f"mongo preflight: {status}")
            if not status.ok:
                click.echo("mongo preflight FAILED", err=True)
                failed = True
    except Exception as e:
        click.echo(f"mongo preflight raised: {e}", err=True)
        failed = True

    try:
        with open_oracle(
            user=oracle_user,
            password=oracle_password,
            dsn=oracle_dsn,
            config_dir=oracle_config_dir,
            wallet_password=oracle_wallet_password,
        ) as bench:
            o_status = bench.preflight()
            click.echo(f"oracle preflight: {o_status}")
            if not o_status.ok:
                click.echo("oracle preflight FAILED", err=True)
                failed = True
    except Exception as e:
        click.echo(f"oracle preflight raised: {e}", err=True)
        failed = True

    sys.exit(1 if failed else 0)


# ─── list ───────────────────────────────────────────────────────────────


@main_group.group(name="list", help="Enumerate harness components.")
def list_group() -> None:
    pass


@list_group.command("scenarios")
def list_scenarios() -> None:
    """Print all registered scenarios with their variant counts."""
    for cls in all_scenarios():
        n_variants = len(cls.variants())
        click.echo(f"{cls.id}  {cls.title}  ({n_variants} variant(s))")


# ─── run ────────────────────────────────────────────────────────────────


@main_group.command("run")
@click.argument("scenario_id")
@click.option("--variant", default=None, help="Variant label; default is the first variant.")
@click.option("--show-pipeline", is_flag=True, help="Print the Mongo pipeline and exit.")
@click.option("--show-sql", is_flag=True, help="Print the Oracle SQL and exit.")
@click.option("--warmup", type=int, default=3, help="Warmup iterations per system.")
@click.option("--iterations", type=int, default=20, help="Measurement iterations per system.")
@click.option("--mongo-uri", default="mongodb://localhost:27017")
@click.option("--mongo-db", default="bench")
@click.option("--oracle-dsn", default=lambda: os.environ.get("ORACLE_DSN", "localhost/FREEPDB1"))
@click.option("--oracle-user", default=lambda: os.environ.get("ORACLE_USER", "BENCH"))
@click.option("--oracle-password", default=lambda: os.environ.get("ORACLE_PASSWORD", "BenchPass2026"))
@click.option(
    "--oracle-config-dir",
    default=lambda: os.environ.get("ORACLE_CONFIG_DIR"),
    help="Wallet directory (tnsnames.ora + cwallet.sso); enables ADB connections.",
)
@click.option(
    "--oracle-wallet-password",
    default=lambda: os.environ.get("ORACLE_WALLET_PASSWORD"),
    help="Wallet password if the wallet is not auto-login (cwallet.sso).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("results/raw"),
    help="Directory to write the run record JSON into.",
)
@click.option("--skip-explain", is_flag=True, help="Skip explain plan capture.")
def run_scenario(
    scenario_id: str,
    variant: str | None,
    show_pipeline: bool,
    show_sql: bool,
    warmup: int,
    iterations: int,
    mongo_uri: str,
    mongo_db: str,
    oracle_dsn: str,
    oracle_user: str,
    oracle_password: str,
    oracle_config_dir: str | None,
    oracle_wallet_password: str | None,
    output_dir: Path,
    skip_explain: bool,
) -> None:
    """Run a scenario.

    Without ``--show-pipeline`` / ``--show-sql``, this requires both engines
    to be up. With those flags, it prints the workload and exits — useful
    for previewing a scenario without spinning up infrastructure.
    """
    cls = get_scenario(scenario_id)
    variants = cls.variants()
    chosen = (
        variants[0] if variant is None else next((v for v in variants if v.label == variant), None)
    )
    if chosen is None:
        click.echo(
            f"unknown variant {variant!r} for {scenario_id}; known: {[v.label for v in variants]}",
            err=True,
        )
        sys.exit(2)

    if show_pipeline:
        click.echo(json.dumps(cls.mongo_pipeline(chosen), indent=2, default=str))
        return
    if show_sql:
        click.echo(cls.oracle_sql(chosen))
        return

    # ── Full execution: open drivers, run scenario_runner, persist record. ──
    from sbe_cte_bench.drivers.mongo import open_mongo
    from sbe_cte_bench.drivers.oracle import open_oracle
    from sbe_cte_bench.runner.scenario_runner import RunConfig
    from sbe_cte_bench.runner.scenario_runner import run_scenario as _run

    cfg = RunConfig(
        warmup_iterations=warmup,
        measurement_iterations=iterations,
        capture_explain=not skip_explain,
    )

    click.echo(f"running {scenario_id} ({chosen.label}) — warmup={warmup} iters={iterations}")

    with (
        open_mongo(uri=mongo_uri, database=mongo_db) as mongo,
        open_oracle(
            user=oracle_user,
            password=oracle_password,
            dsn=oracle_dsn,
            config_dir=oracle_config_dir,
            wallet_password=oracle_wallet_password,
        ) as oracle,
    ):
        record = _run(scenario_cls=cls, variant=chosen, mongo=mongo, oracle=oracle, config=cfg)

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_label = chosen.label.replace("/", "_").replace(" ", "_")
    timestamp = record.timestamp.strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"{scenario_id}-{safe_label}-{timestamp}.json"
    output_path.write_text(record.model_dump_json(by_alias=True, indent=2))

    click.echo(f"\nresult: {scenario_id} {chosen.label}")
    click.echo(
        f"  mongo  median={record.mongo.median_ms:.2f} ms  "
        f"p95={record.mongo.p95_ms:.2f} ms  cv={record.mongo.cv:.3f}"
    )
    click.echo(
        f"  oracle median={record.oracle.median_ms:.2f} ms  "
        f"p95={record.oracle.p95_ms:.2f} ms  cv={record.oracle.cv:.3f}"
    )
    if record.oracle.median_ms > 0:
        ratio = record.mongo.median_ms / record.oracle.median_ms
        click.echo(f"  ratio  mongo/oracle = {ratio:.2f}x")
    eq_text = "MATCH" if record.equivalence.match else "MISMATCH"
    click.echo(
        f"  equivalence: {eq_text}  "
        f"(mongo={record.equivalence.row_count_mongo} rows, "
        f"oracle={record.equivalence.row_count_oracle} rows)"
    )
    verdict = "PASS" if record.prediction.pass_ else "FAIL"
    click.echo(f"  prediction: {verdict}  ({record.prediction.claim})")
    click.echo(f"\nrecord: {output_path}")


# ─── report ─────────────────────────────────────────────────────────────


@main_group.group(name="report", help="Aggregate raw run records into reports.")
def report_group() -> None:
    pass


@report_group.command("aggregate")
@click.option(
    "--raw-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("results/raw"),
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("results/processed/summary.csv"),
)
def report_aggregate(raw_dir: Path, output: Path) -> None:
    """Aggregate raw run records into a CSV summary."""
    from sbe_cte_bench.reporting.aggregate import aggregate_runs

    aggregate_runs(raw_dir, output)
    click.echo(f"wrote {output}")


@report_group.command("scenario")
@click.argument("run_record_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def report_scenario(run_record_path: Path) -> None:
    """Render a per-scenario markdown writeup from a run record JSON file."""
    from sbe_cte_bench.reporting.markdown import render_scenario_writeup

    record = json.loads(run_record_path.read_text())
    click.echo(render_scenario_writeup(record))


@report_group.command("all")
@click.option(
    "--raw-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("results/raw"),
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("results/processed/REPORT.md"),
)
@click.option("--scale-factor", default="SF0.001", help="Scale factor label for the report header.")
def report_all(raw_dir: Path, output: Path, scale_factor: str) -> None:
    """Generate a consolidated cross-scenario report.

    Walks ``raw_dir``, picks the latest run per (scenario, variant), produces
    a single markdown report at ``output`` summarizing the architectural
    findings, top/bottom ratios, and prediction outcomes.
    """
    from sbe_cte_bench.reporting.cross_scenario import write_report

    result = write_report(raw_dir, output, scale_factor=scale_factor)
    click.echo(f"wrote {result}")


@report_group.command("html")
@click.option(
    "--raw-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("results/raw"),
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("results/processed/dashboard.html"),
)
@click.option("--scale-factor", default="SF1", help="Scale factor label for the dashboard header.")
def report_html(raw_dir: Path, output: Path, scale_factor: str) -> None:
    """Render a self-contained HTML dashboard.

    Walks ``raw_dir`` for the latest record per (scenario, variant) and emits
    a single HTML file at ``output`` containing per-scenario tabs (Mongo
    pipeline / Mongo explain / Oracle SQL / Oracle plan / SQL Monitor link)
    with Chart.js comparison plots. Self-contained except for Chart.js
    loaded from CDN. SQL Monitor active reports are linked relatively
    from ``results/sql_monitor/``.
    """
    from sbe_cte_bench.reporting.html_dashboard import render_dashboard

    render_dashboard(raw_dir=raw_dir, output=output, scale_factor=scale_factor)
    click.echo(f"wrote {output}")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns 0 on success."""
    try:
        main_group.main(args=argv, standalone_mode=False)
    except click.exceptions.UsageError as exc:
        click.echo(f"usage error: {exc.format_message()}", err=True)
        return 2
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
