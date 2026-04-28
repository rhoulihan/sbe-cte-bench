"""Tests for the click-based CLI surface.

Verifies subcommands resolve, --help text renders, and ``--show-pipeline`` /
``--show-sql`` produce structured output without requiring live engines.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sbe_cte_bench import __version__
from sbe_cte_bench.cli import main_group


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.unit
def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.unit
def test_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["--help"])
    assert result.exit_code == 0
    for sub in ("data", "infra", "list", "report", "run"):
        assert sub in result.output


@pytest.mark.unit
def test_list_scenarios_prints_all(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["list", "scenarios"])
    assert result.exit_code == 0
    for sid in ("S01", "S02", "S03", "S04", "S05", "S14"):
        assert sid in result.output


@pytest.mark.unit
def test_run_show_pipeline_returns_json(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["run", "S01", "--show-pipeline"])
    assert result.exit_code == 0, result.output
    pipeline = json.loads(result.output)
    assert isinstance(pipeline, list)
    assert pipeline[0].get("$match") is not None


@pytest.mark.unit
def test_run_show_sql_returns_text(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["run", "S02", "--show-sql"])
    assert result.exit_code == 0
    assert "WITH" in result.output


@pytest.mark.unit
def test_run_unknown_scenario_returns_error(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["run", "S99", "--show-sql"])
    assert result.exit_code != 0


@pytest.mark.unit
def test_run_unknown_variant_returns_error(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["run", "S03", "--variant", "k=999", "--show-pipeline"])
    assert result.exit_code != 0
    assert "unknown variant" in result.output


@pytest.mark.unit
def test_run_with_specific_variant_succeeds(runner: CliRunner) -> None:
    result = runner.invoke(main_group, ["run", "S03", "--variant", "k=4", "--show-pipeline"])
    assert result.exit_code == 0
    pipeline = json.loads(result.output)
    # Position 4 means the boundary marker ($redact) at index 3.
    assert "$redact" in pipeline[3]


@pytest.mark.unit
def test_data_generate_runs(runner: CliRunner, tmp_path: object) -> None:
    result = runner.invoke(
        main_group,
        ["data", "generate", "--scale", "SF0.001", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "generated" in result.output


@pytest.mark.unit
def test_report_aggregate_handles_empty_dir(runner: CliRunner, tmp_path: object) -> None:
    raw_dir = tmp_path / "raw"  # type: ignore[operator]
    raw_dir.mkdir()
    output = tmp_path / "summary.csv"  # type: ignore[operator]
    result = runner.invoke(
        main_group,
        ["report", "aggregate", "--raw-dir", str(raw_dir), "--output", str(output)],
    )
    assert result.exit_code == 0
    assert output.exists()
