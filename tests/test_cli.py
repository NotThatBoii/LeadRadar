import json

from typer.testing import CliRunner

from leadradar.cli import app
from leadradar.models import RunReport

runner = CliRunner()


def invoke(tmp_path, *args):
    return runner.invoke(app, ["--db", str(tmp_path / "cli.db"), *args])


def test_sources_lists_adapters_without_db(tmp_path):
    result = invoke(tmp_path, "sources")
    assert (
        result.exit_code == 0 and "directory-js" in result.stdout and "SYNTHETIC" in result.stdout
    )
    assert not (tmp_path / "cli.db").exists()


def test_cli_collect_list_stats_and_exports(tmp_path):
    first = invoke(tmp_path, "collect", "--source", "fixture", "--limit", "100")
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["new"] == 4
    second = invoke(tmp_path, "collect", "--source", "fixture")
    assert second.exit_code == 0 and json.loads(second.stdout)["duplicates"] == 5
    assert json.loads(invoke(tmp_path, "list").stdout) == []
    assert len(json.loads(invoke(tmp_path, "list", "--kind", "fixture").stdout)) == 4
    assert json.loads(invoke(tmp_path, "stats").stdout)["fixture_businesses"] == 4
    for format in ("csv", "json"):
        output = tmp_path / f"fixture.{format}"
        result = invoke(
            tmp_path, "export", "--kind", "fixture", "--format", format, "--output", str(output)
        )
        assert result.exit_code == 0 and output.exists(), result.output


def test_cli_requires_geographic_scope(tmp_path):
    result = invoke(tmp_path, "collect", "--source", "overpass")
    assert result.exit_code == 1 and "--bbox is required" in result.output


def test_cli_rejects_unknown_source_and_unbounded_limits(tmp_path):
    assert invoke(tmp_path, "collect", "--source", "not-real").exit_code == 2
    assert invoke(tmp_path, "collect", "--source", "fixture", "--limit", "501").exit_code == 2


def test_cli_requires_directory_policy_config(tmp_path):
    result = invoke(tmp_path, "collect", "--source", "directory")
    assert result.exit_code == 1 and "--directory-config" in result.output


def test_cli_refuses_existing_output(tmp_path):
    path = tmp_path / "existing.csv"
    path.write_text("keep")
    result = invoke(tmp_path, "export", "--output", str(path))
    assert result.exit_code == 1 and path.read_text() == "keep"


def test_cli_failed_run_reports_error_without_catching_own_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "leadradar.cli.run_collection",
        lambda *args: RunReport(
            source="fixture", status="failed", errors=["Simulated unavailable source"]
        ),
    )
    result = invoke(tmp_path, "collect", "--source", "fixture")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "failed"
    assert "Error: 1" not in result.output


def test_cli_preserves_interruption_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "leadradar.cli.run_collection",
        lambda *args: RunReport(source="fixture", status="interrupted"),
    )
    result = invoke(tmp_path, "collect", "--source", "fixture")
    assert result.exit_code == 130
    assert json.loads(result.stdout)["status"] == "interrupted"
