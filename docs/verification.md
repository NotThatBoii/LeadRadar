# Verification

Verified locally on **2026-10-18**, Windows, Python **3.12.14**. Client timezone: Asia/Manila.
Fixture data is synthetic; live OpenStreetMap results are kept separately in ignored runtime files.
CI status is recorded by GitHub on the pull request; local results do not imply CI completion.

## Real-world collection

Source: public Overpass API, `https://overpass-api.de/api/interpreter`.
Policy review: [operator guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)
and [OSM copyright](https://www.openstreetmap.org/copyright), reviewed before implementation.
Query: named `amenity=restaurant` businesses within `14.54,121.00,14.59,121.05` around Makati,
Philippines; output limit 150, no source geography inferred from that target.

```powershell
.\.venv\Scripts\leadradar.exe --db data/live.db collect --source overpass --bbox '14.54,121.00,14.59,121.05' --tag amenity=restaurant --limit 150
```

Actual successful-run evidence (excerpts of CLI JSON):

| Run | Manila start/end | Processed | New | Updated | Duplicates | Failed records | HTTP attempts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| First successful live collection | 17:40:37–17:41:15 | 150 | 150 | 0 | 0 | 0 | 1 |
| Same live query repeated | 17:41:57–17:42:29 | 150 | 0 | 0 | 150 | 0 | 2 |

Both reports have `status: success`; SQLite still contains **150 unique live businesses** and
150 source observations. The repeated live run recovered from one HTTP 502 automatically.
An earlier implementation probe failed with transport errors and a compressed-response decoding
bug, stored zero records, and remains visible as a failed run. The bug was fixed and a gzip
regression test was executed; it is not an outstanding limitation.

One actual source record was **Phò Bac**, `node/251456679`, category `restaurant`:
[OSM source](https://www.openstreetmap.org/node/251456679).
Attribution: **© OpenStreetMap contributors**, data licensed under
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
No contact information is published in this repository.

Field coverage in this particular sample: **9 websites**, **122 city values**, and **7 public phone
values** across 150 listings. Missing values were not enriched or invented. These are public
business listings, not 150 verified contactable companies or permission to contact them.

Verified runtime artifacts (ignored by Git):

- `data/live.db`: SQLite `PRAGMA integrity_check` returned `ok`.
- `exports/makati-restaurants.csv`: 150 data rows, UTF-8 BOM and attribution/license columns.
- `exports/makati-restaurants.json`: 150 objects with structured provenance and observations.
- All 150 stored rows carry OSM attribution. Unique database IDs and both export counts were checked.

Exports were created after the first successful run. A repeat updates last-seen timestamps in
SQLite; create exports with a fresh filename if a more recent last-seen value is needed.

## Offline fixture and failure recovery

Executed `python examples/demo.py`. This uses actual two-page parsing and the production HTTP retry
transport with `httpx.MockTransport`, never a live service pretending to be fixture data.

```text
first_run:  status=success processed=5 new=4 updated=0 duplicates=1 failed=0 requests=3
second_run: status=success processed=5 new=0 updated=0 duplicates=5 failed=0 requests=0
unique_synthetic_businesses=4
simulated_503_recovered=true
```

The first page response deliberately returns HTTP 503 once, then succeeds. The three requests are
the failed first attempt, successful first page, and successful second page. Both exports, SQLite,
and a machine-readable report are created under a fresh `artifacts/fixture-demo-*` directory.
These outputs are visibly marked synthetic and cannot appear in default live exports.

## Tests, lint, and packaging

The full suite, including actual Chromium rendering and pagination, was executed locally:
**97 passed, 0 failed, 0 skipped** in 7.26 seconds, with **91% statement coverage** (713 statements).
This duration describes one local run, not a benchmark. External HTTP is prohibited in unit tests. The browser fixture
uses real JavaScript execution with mocked HTML delivered through the guarded transport.

```text
collected 97 items
97 passed in 7.26s
TOTAL 713 statements, 64 missed, 91% coverage
All checks passed!
31 files already formatted
```

- Ruff lint: passed (`python -m ruff check .`).
- Ruff formatting: passed (`python -m ruff format --check .`).
- Dependency consistency: `python -m pip check` reported no broken requirements.
- Editable package install: passed.
- Source distribution and wheel build: passed (`python -m build --no-isolation`).
- Built wheel installed in a separate target directory; imports resolved to that wheel installation,
  and the packaged multi-page fixture was parsed successfully without reading `src/`.
- Tracked-file checks exclude databases, exports, logs, local browsers, temporary files, and secrets.

Windows sandbox process/file restrictions required permission to run pip's builds and Chromium.
The optional headless-shell download hit low disk space; the installed full Chromium was successfully
tested with `channel="chromium"`. Installation now documents `--no-shell` to avoid that extra download.

## Manual reproduction

1. Follow the PowerShell installation commands in the README.
2. Run `python examples/demo.py`; inspect its printed output directory, report, CSV, JSON, and SQLite.
3. Run the test and Ruff commands from the README; install Chromium for the browser-marked test.
4. For a live check, run the bounded command above, inspect `stats`, then export to fresh filenames.
5. If you repeat a live query for verification, expect zero new records when the source is unchanged;
   do not loop requests or use this public instance as a production service backend.

Limits remain: source accuracy/coverage is unverified; public Overpass availability varies; matching
is conservative; HTML adapters require individually reviewed permission, stable IDs, and appropriate
selectors. No other third-party directory was live-tested. Stage 2 and Stage 3 are unimplemented.
