# Architecture and persistence decisions

`sources.Source` is a small iterator protocol. `OverpassSource`, `DirectorySource`, and
`FixtureSource` produce `RawBusiness` observations. Adapters handle pagination and source shape;
the runner owns validation, counters, interruption, and report finalization. A failed later page
returns a partial run with previously committed rows intact. Unexpected exceptions produce a
failed report with the exception class; record data is not logged.

`HttpCollector` uses streaming httpx responses to bound downloaded/decompressed bytes. It counts
every attempt, applies a monotonic-clock rate interval, retries only transport failures and
429/500/502/503/504, honors Retry-After up to 60 seconds, and refuses redirects/access denials.
It never retries a 401/403 or switches servers to work around a block. The JS renderer routes all
permitted GET document/script/fetch/XHR requests through this same transport and robots guard.
It blocks other resource types, non-GET methods, service workers, and WebSockets.

Directory configs record a human source-policy review. Robots is an additional restriction,
not permission. A missing robots file (404) is allowed; inaccessible robots fails closed.
Crawl-delay/Request-rate can increase the request interval. Pagination must stay on the configured
origin and cannot cycle. Card IDs must be stable to avoid replacing records when page order changes.

`processing.normalize` returns a Pydantic `Business`. An invalid optional website is omitted from
the normalized fields and flagged, while its original value remains in provenance. It does not
visit company websites or infer geography from the selected bounding box.

The three SQLite tables are:

| Table | Identity/constraints | Contents |
| --- | --- | --- |
| `businesses` | Integer PK, unique SHA-256 identity | Primary normalized payload, indexed domain/region/category, kind, three timestamps |
| `observations` | Composite PK `(source, source_record_id)`, business FK | Original + normalized latest source observation and first/last seen |
| `collection_runs` | Integer PK | Source, start/end/status, JSON counters/errors/warnings |

Matching first consults the source identity, then a domain/name/precise-location signature.
Fixtures and live data cannot share a signature. No public suffix list or fuzzy name heuristic
is used. A shared corporate domain does not erase branch locations. The signature is stable from
first insertion; source edits can make it stale, a deliberate conservative tradeoff that can leave
additional duplicates until explicit reconciliation is built. The first source is authoritative;
conflicting secondary observations do not replace it. Observation history is latest-state, not a
complete audit log. Adding/changing a secondary observation counts as an update, while an unchanged
repeat counts as a duplicate. Indexes support filters and foreign-key lookup.

`Database.upsert` wraps identity lookup, insert/update, and observation upsert in one immediate
transaction. `PRAGMA foreign_keys=ON`, a busy timeout, WAL, and uniqueness constraints protect local
consistency. Numbered migration SQL runs atomically and advances `user_version`. No destructive
automatic migrations exist. Parallel collection processes are not a supported high-throughput mode.

Export reads the database's unique business view and includes all observations. CSV uses pandas;
JSON uses the standard library. A temporary file, exclusive output reservation, and atomic replacement
publish complete exports without replacing existing user files. A forced process termination between
reservation and replacement can leave an empty reserved output; use a fresh output filename.
Spreadsheet escaping affects exported strings, including nested original-values payloads,
but never mutates SQLite. JSON logs go to stderr so stdout stays machine-readable.
