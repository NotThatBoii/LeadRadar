import json
import sqlite3
from pathlib import Path

from leadradar.models import Business, RunReport, utc_now

MIGRATIONS = [
    """
CREATE TABLE businesses (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('live','fixture')),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    domain TEXT,
    category TEXT,
    region TEXT,
    first_collected_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX idx_business_domain ON businesses(domain);
CREATE INDEX idx_business_filters ON businesses(source_kind, region, category);
CREATE TABLE observations (
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    first_collected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(source, source_record_id)
);
CREATE INDEX idx_observation_business ON observations(business_id);
CREATE TABLE collection_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX idx_runs_started ON collection_runs(started_at);
"""
]


def comparable(payload: dict) -> dict:
    """Moving a card between pages is not a change to the business itself."""
    content = {k: v for k, v in payload.items() if k not in {"source_url", "identity_key"}}
    content["original_values"] = {
        k: v for k, v in content.get("original_values", {}).items() if k != "source_url"
    }
    return content


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        try:
            self.migrate()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def migrate(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError("Database schema is newer than this application")
        for index in range(version, len(MIGRATIONS)):
            try:
                self.connection.executescript(
                    f"BEGIN IMMEDIATE;\n{MIGRATIONS[index]}\n"
                    f"PRAGMA user_version={index + 1};\nCOMMIT;"
                )
            except BaseException:
                self.connection.rollback()
                raise

    def start_run(self, report: RunReport):
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO collection_runs(source,started_at,status,payload) VALUES(?,?,?,?)",
                (report.source, report.started_at, report.status, report.model_dump_json()),
            )
            report.id = cursor.lastrowid

    def finish_run(self, report: RunReport):
        report.ended_at = utc_now()
        with self.connection:
            self.connection.execute(
                "UPDATE collection_runs SET ended_at=?,status=?,payload=? WHERE id=?",
                (report.ended_at, report.status, report.model_dump_json(), report.id),
            )

    def upsert(self, business: Business) -> str:
        """Commit each record atomically, preserving identities and every source observation."""
        incoming = business.model_dump()
        encoded, now = json.dumps(incoming, ensure_ascii=False, sort_keys=True), utc_now()
        conn = self.connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            observation = conn.execute(
                "SELECT * FROM observations WHERE source=? AND source_record_id=?",
                (business.source, business.source_record_id),
            ).fetchone()
            if observation:
                row = conn.execute(
                    "SELECT * FROM businesses WHERE id=?", (observation["business_id"],)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM businesses WHERE identity_key=?", (business.identity_key,)
                ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """INSERT INTO businesses(
                       identity_key,source_kind,payload,domain,category,region,
                       first_collected_at,last_updated_at,last_seen_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        business.identity_key,
                        business.source_kind,
                        encoded,
                        business.domain,
                        business.category,
                        business.region,
                        now,
                        now,
                        now,
                    ),
                )
                business_id, outcome = cursor.lastrowid, "new"
            else:
                business_id = row["id"]
                current = json.loads(row["payload"])
                # The first source is authoritative; additional observations retain conflicting
                # values in full without silently overwriting the primary record.
                primary = (current["source"], current["source_record_id"])
                if primary == (business.source, business.source_record_id):
                    # The first matching signature stays stable across source edits.
                    incoming["identity_key"] = row["identity_key"]
                    encoded_primary = json.dumps(incoming, ensure_ascii=False, sort_keys=True)
                    changed = comparable(incoming) != comparable(current)
                    if not changed:
                        encoded_primary = row["payload"]
                else:
                    encoded_primary = row["payload"]
                    changed = observation is None or comparable(
                        json.loads(observation["payload"])
                    ) != comparable(incoming)
                outcome = "updated" if changed else "duplicates"
                values = json.loads(encoded_primary)
                conn.execute(
                    """UPDATE businesses SET payload=?,domain=?,category=?,region=?,
                       last_updated_at=?,last_seen_at=? WHERE id=?""",
                    (
                        encoded_primary,
                        values["domain"],
                        values["category"],
                        values["region"],
                        now if changed else row["last_updated_at"],
                        now,
                        business_id,
                    ),
                )
            conn.execute(
                """INSERT INTO observations VALUES(?,?,?,?,?,?)
                   ON CONFLICT(source,source_record_id) DO UPDATE SET
                   payload=excluded.payload,last_seen_at=excluded.last_seen_at""",
                (business.source, business.source_record_id, business_id, encoded, now, now),
            )
            conn.commit()
            return outcome
        except BaseException:
            conn.rollback()
            raise

    def records(
        self,
        *,
        kind: str = "live",
        region: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        sql, params = "SELECT * FROM businesses WHERE source_kind=?", [kind]
        for column, value in (("region", region), ("category", category)):
            if value:
                sql += f" AND {column} = ? COLLATE NOCASE"
                params.append(value)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        results = []
        for row in self.connection.execute(sql, params).fetchall():
            record = json.loads(row["payload"])
            record.update(
                {
                    key: row[key]
                    for key in ("id", "first_collected_at", "last_updated_at", "last_seen_at")
                }
            )
            record["observations"] = [
                json.loads(item[0])
                for item in self.connection.execute(
                    "SELECT payload FROM observations WHERE business_id=? "
                    "ORDER BY source,source_record_id",
                    (row["id"],),
                )
            ]
            results.append(record)
        return results

    def stats(self) -> dict:
        counts = {
            row[0]: row[1]
            for row in self.connection.execute(
                "SELECT source_kind,COUNT(*) FROM businesses GROUP BY source_kind"
            )
        }
        runs = [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT payload FROM collection_runs ORDER BY id DESC LIMIT 10"
            )
        ]
        return {
            "live_businesses": counts.get("live", 0),
            "fixture_businesses": counts.get("fixture", 0),
            "observations": self.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[
                0
            ],
            "recent_runs": runs,
        }
