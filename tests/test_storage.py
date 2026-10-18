import sqlite3

import pytest

from leadradar.collectors.runner import collect
from leadradar.processing.normalize import normalize
from leadradar.sources.directory import FixtureSource
from leadradar.storage.database import Database


def test_repeated_collection_is_idempotent(db):
    first = collect(FixtureSource(), db, 100)
    second = collect(FixtureSource(), db, 100)
    assert (first.processed, first.new, first.duplicates) == (5, 4, 1)
    assert (second.processed, second.new, second.updated, second.duplicates) == (5, 0, 0, 5)
    assert db.stats()["fixture_businesses"] == 4
    assert db.stats()["live_businesses"] == 0
    assert len(db.stats()["recent_runs"]) == 2


def test_upsert_preserves_first_timestamp_and_updates_changed_data(db, raw):
    assert db.upsert(normalize(raw)) == "new"
    before = db.records(kind="fixture")[0]
    assert db.upsert(normalize(raw.model_copy(update={"phone": "+00 555 1234"}))) == "updated"
    after = db.records(kind="fixture")[0]
    assert after["first_collected_at"] == before["first_collected_at"]
    assert after["last_updated_at"] >= before["last_updated_at"]
    assert after["phone"] == "+00 555 1234"


def test_unchanged_repeat_does_not_change_last_updated(db, raw):
    db.upsert(normalize(raw))
    before = db.records(kind="fixture")[0]
    assert db.upsert(normalize(raw)) == "duplicates"
    after = db.records(kind="fixture")[0]
    assert after["last_updated_at"] == before["last_updated_at"]
    assert after["last_seen_at"] >= before["last_seen_at"]


def test_source_identity_survives_name_domain_and_address_change(db, raw):
    db.upsert(normalize(raw))
    changed = normalize(
        raw.model_copy(
            update={
                "website": "changed.example",
                "business_name": "Renamed",
                "address": "999 Example Street",
            }
        )
    )
    assert db.upsert(changed) == "updated"
    assert db.upsert(changed) == "duplicates"
    assert len(db.records(kind="fixture")) == 1


def test_cross_source_duplicate_retains_both_provenances(db, raw):
    db.upsert(normalize(raw))
    other = raw.model_copy(
        update={"source": "other", "source_record_id": "other-2", "phone": "999"}
    )
    assert db.upsert(normalize(other)) == "updated"
    assert db.upsert(normalize(other)) == "duplicates"
    records = db.records(kind="fixture")
    assert len(records) == 1 and len(records[0]["observations"]) == 2
    assert records[0]["phone"] is None
    assert records[0]["observations"][0]["phone"] == "999"


def test_shared_domain_branches_are_preserved(db, raw):
    db.upsert(normalize(raw))
    branch = raw.model_copy(update={"source_record_id": "2", "address": "2 Other Road"})
    db.upsert(normalize(branch))
    assert len(db.records(kind="fixture")) == 2


def test_card_moving_between_pages_is_not_content_update(db, raw):
    db.upsert(normalize(raw))
    before = db.records(kind="fixture")[0]
    moved = raw.model_copy(update={"source_url": "https://directory.example/page2#1"})
    assert db.upsert(normalize(moved)) == "duplicates"
    after = db.records(kind="fixture")[0]
    assert before["last_updated_at"] == after["last_updated_at"]
    assert after["observations"][0]["source_url"] == moved.source_url


def test_missing_website_uses_stable_source_id(db, raw):
    record = normalize(raw.model_copy(update={"website": None}))
    assert db.upsert(record) == "new"
    assert db.upsert(record) == "duplicates"


def test_filters_are_parameterized_and_case_insensitive(db, raw):
    db.upsert(normalize(raw))
    assert len(db.records(kind="fixture", category="CAFE", region="demo region")) == 1
    assert db.records(kind="fixture", region="' OR 1=1 --") == []


def test_file_persistence_and_schema_reopen(tmp_path, raw):
    path = tmp_path / "nested" / "business.db"
    with Database(path) as db:
        db.upsert(normalize(raw))
    with Database(path) as db:
        assert len(db.records(kind="fixture")) == 1
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_newer_schema_rejected(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_validation_failure_counted_while_next_record_continues(db, raw):
    class Mixed:
        name = "mixed"

        def collect(self, limit):
            yield raw.model_copy(update={"business_name": " "})
            yield raw

    report = collect(Mixed(), db, 10)
    assert report.status == "partial"
    assert (report.processed, report.failed, report.new) == (2, 1, 1)


def test_rollback_leaves_database_usable_after_invalid_observation(db, raw):
    db.upsert(normalize(raw))
    with pytest.raises(sqlite3.IntegrityError), db.connection:
        db.connection.execute(
            "INSERT INTO observations VALUES(?,?,?,?,?,?)", ("bad", "id", 999, "{}", "now", "now")
        )
    assert db.upsert(normalize(raw)) == "duplicates"
