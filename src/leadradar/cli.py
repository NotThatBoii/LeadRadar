import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from leadradar.collectors.http import HttpCollector
from leadradar.collectors.runner import collect as run_collection
from leadradar.config import DirectoryConfig, RequestPolicy
from leadradar.exporters.files import export_records
from leadradar.logging import configure_logging
from leadradar.sources.directory import DirectorySource, FixtureSource
from leadradar.sources.overpass import OverpassSource
from leadradar.storage.database import Database

app = typer.Typer(no_args_is_help=True, help="Build attributed public business lists responsibly.")


class SourceName(StrEnum):
    overpass = "overpass"
    fixture = "fixture"
    directory = "directory"
    directory_js = "directory-js"


class Kind(StrEnum):
    live = "live"
    fixture = "fixture"


class ExportFormat(StrEnum):
    csv = "csv"
    json = "json"


@app.callback()
def main(
    ctx: typer.Context,
    db: Annotated[
        Path,
        typer.Option(
            help="SQLite path; fixture data stays separately labelled.", envvar="LEADRADAR_DB"
        ),
    ] = Path("data/leadradar.db"),
):
    ctx.obj = db
    configure_logging()


@app.command()
def sources():
    """Show adapters and permission requirements."""
    typer.echo("overpass      Live OSM business API; --bbox required, ODbL attribution included")
    typer.echo("fixture       Packaged 2-page SYNTHETIC HTML directory; no network")
    typer.echo("directory     Static public HTML; --directory-config with reviewed permission")
    typer.echo("directory-js  JS-rendered public HTML; same policy plus Playwright Chromium")


@app.command()
def collect(
    ctx: typer.Context,
    source: Annotated[SourceName, typer.Option(help="Source adapter")],
    limit: Annotated[int, typer.Option(min=1, max=500)] = 100,
    bbox: Annotated[str | None, typer.Option(help="south,west,north,east; <=0.5° per side")] = None,
    tag: Annotated[str, typer.Option(help="OSM business key=value")] = "amenity=restaurant",
    directory_config: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    max_requests: Annotated[int, typer.Option(min=1, max=100)] = 20,
    interval: Annotated[float, typer.Option(min=1, max=60)] = 2,
    timeout: Annotated[float, typer.Option(min=1, max=120)] = 45,
):
    """Collect bounded public records. Non-success outcomes return a nonzero exit code."""
    policy = RequestPolicy(
        interval=max(5, interval) if source == SourceName.overpass else interval,
        max_requests=max_requests,
        timeout=timeout,
    )
    http = HttpCollector(policy)
    try:
        if source == SourceName.fixture:
            adapter = FixtureSource()
        elif source == SourceName.overpass:
            if not bbox:
                raise ValueError("--bbox is required, e.g. --bbox '14.54,121.00,14.59,121.05'")
            adapter = OverpassSource(http, bbox, tag)
        else:
            if not directory_config:
                raise ValueError("--directory-config is required; see docs/sources.md")
            config = DirectoryConfig.model_validate_json(
                directory_config.read_text(encoding="utf-8")
            )
            adapter = DirectorySource(http, config, javascript=source == SourceName.directory_js)
        with Database(ctx.obj) as database:
            report = run_collection(adapter, database, limit, http)
        typer.echo(report.model_dump_json(indent=2))
        if report.status != "success":
            raise typer.Exit(130 if report.status == "interrupted" else 1)
    except typer.Exit:
        raise
    except (ValueError, ValidationError, OSError, sqlite3.Error, RuntimeError) as exc:
        # Pydantic's full error text includes raw input, which may contain credentials.
        message = (
            "Invalid directory configuration; inspect documented fields"
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(1) from exc
    finally:
        http.close()


def read_records(ctx, kind, region=None, category=None, limit=None):
    try:
        with Database(ctx.obj) as database:
            return database.records(kind=kind, region=region, category=category, limit=limit)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command(name="list")
def list_businesses(
    ctx: typer.Context,
    kind: Annotated[Kind, typer.Option()] = Kind.live,
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 20,
    region: Annotated[str | None, typer.Option()] = None,
    category: Annotated[str | None, typer.Option()] = None,
):
    """Inspect stored records as JSON; defaults to live data."""
    typer.echo(
        json.dumps(read_records(ctx, kind, region, category, limit), ensure_ascii=False, indent=2)
    )


@app.command()
def stats(ctx: typer.Context):
    """Show live/fixture counts and the ten most recent collection runs."""
    try:
        with Database(ctx.obj) as database:
            typer.echo(json.dumps(database.stats(), ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        typer.echo(f"Database error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def export(
    ctx: typer.Context,
    format: Annotated[ExportFormat, typer.Option()] = ExportFormat.csv,
    output: Annotated[
        Path | None, typer.Option(help="New filename; existing files are protected")
    ] = None,
    kind: Annotated[Kind, typer.Option()] = Kind.live,
    region: Annotated[str | None, typer.Option()] = None,
    category: Annotated[str | None, typer.Option()] = None,
):
    """Export all matching unique businesses with attribution and safe spreadsheet values."""
    output = output or Path(f"exports/{kind}-leads.{format}")
    records = read_records(ctx, kind, region, category)
    try:
        count = export_records(records, output, format)
    except (OSError, ValueError) as exc:
        typer.echo(f"Export error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Exported {count} {kind} businesses to {output}")
