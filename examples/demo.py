"""Offline end-to-end demonstration; all generated records are SYNTHETIC."""

import json
import tempfile
from pathlib import Path

import httpx

from leadradar.collectors.http import HttpCollector
from leadradar.collectors.runner import collect
from leadradar.config import RequestPolicy
from leadradar.exporters.files import export_records
from leadradar.logging import configure_logging
from leadradar.sources.directory import FixtureSource
from leadradar.storage.database import Database


def main():
    configure_logging()
    Path("artifacts").mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="fixture-demo-", dir="artifacts"))
    attempts = 0

    def simulated_server(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="SIMULATED service unavailable")
        return httpx.Response(200, text=FixtureSource.load(str(request.url)))

    # Use the real retry transport over a deterministic simulated HTTP server.
    client = httpx.Client(transport=httpx.MockTransport(simulated_server))
    http = HttpCollector(RequestPolicy(interval=0, backoff=0), client=client, sleep=lambda _: None)
    source = FixtureSource(loader=lambda url: http.request("GET", url).text)
    with client, Database(output / "fixture.db") as db:
        first = collect(source, db, 100, http)
        second = collect(FixtureSource(), db, 100)
        records = db.records(kind="fixture")
        assert first.status == second.status == "success"
        assert (first.new, first.duplicates, len(records)) == (4, 1, 4)
        assert (second.new, second.updated, second.duplicates) == (0, 0, 5)
        assert first.requests == 3 and first.warnings
        export_records(records, output / "synthetic.csv", "csv")
        export_records(records, output / "synthetic.json", "json")
        evidence = {
            "kind": "SYNTHETIC FIXTURE — NOT REAL LEADS",
            "output": str(output),
            "first_run": first.model_dump(),
            "second_run": second.model_dump(),
            "unique_synthetic_businesses": len(records),
            "simulated_503_recovered": True,
        }
        (output / "report.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
