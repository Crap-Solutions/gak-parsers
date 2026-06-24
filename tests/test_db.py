"""Tests for tickets/lib/db.py connection handling.

Covers the busy-timeout behaviour: SQLite has no network connect timeout,
so the relevant timeout is the *busy* timeout (how long a connection waits
for a lock). All connections go through open_connection() and must set a
consistent busy_timeout, so cron jobs fail after DB_TIMEOUT instead of
hanging on lock contention.
"""
import sqlite3
import time

import pytest

from lib import db


# --- schema & connection basics ---

def test_init_db_creates_schema(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"EVENTS", "ENTRIES"} <= tables


def test_init_db_round_trip(tmp_path):
    """init_db'd connection can actually write and read back."""
    conn = db.init_db(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE) VALUES ('m', 7, 3)")
    conn.commit()
    row = conn.execute("SELECT sold, available FROM entries WHERE match='m'").fetchone()
    conn.close()
    assert row == (7, 3)


# --- busy timeout is set consistently ---

def test_open_connection_sets_busy_timeout(tmp_path):
    conn = db.open_connection(str(tmp_path / "t.db"))
    ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert ms == db.DB_TIMEOUT * 1000


def test_init_db_sets_busy_timeout(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert ms == db.DB_TIMEOUT * 1000


def test_open_connection_read_only_sets_busy_timeout(tmp_path):
    # create the db first so the read-only open succeeds
    w = db.init_db(str(tmp_path / "t.db"))
    w.close()
    conn = db.open_connection(str(tmp_path / "t.db"), read_only=True)
    ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert ms == db.DB_TIMEOUT * 1000


# --- read-only really is read-only ---

def test_open_connection_read_only_cannot_write(tmp_path):
    w = db.init_db(str(tmp_path / "t.db"))
    w.close()
    conn = db.open_connection(str(tmp_path / "t.db"), read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_fail (a INT)")
    conn.close()


def test_open_connection_read_only_missing_db_fails(tmp_path):
    """A read-only open of a non-existent DB must not silently create one."""
    missing = str(tmp_path / "nope" / "does_not_exist.db")
    with pytest.raises(sqlite3.OperationalError):
        db.open_connection(missing, read_only=True)


# --- the timeout is actually honored under contention ---

def test_busy_timeout_honored_under_contention(tmp_path, monkeypatch):
    """A second writer raises OperationalError after the busy timeout elapses,
    rather than hanging indefinitely or failing instantly."""
    # shorten the timeout so the test stays fast
    monkeypatch.setattr(db, "DB_TIMEOUT", 0.5)

    db_path = str(tmp_path / "lock.db")
    db.init_db(db_path).close()

    # Hold a RESERVED lock with busy_timeout=0 so the holder never waits.
    holder = sqlite3.connect(db_path, isolation_level=None, timeout=0.1)
    holder.execute("PRAGMA busy_timeout = 0")
    holder.execute("BEGIN IMMEDIATE")
    try:
        contender = db.open_connection(db_path)
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            contender.execute(
                "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE) VALUES ('x',1,2)")
        elapsed = time.monotonic() - start
        contender.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    # waited roughly DB_TIMEOUT, not zero, not forever
    assert 0.3 <= elapsed < 3.0, f"unexpected wait {elapsed:.2f}s"


# --- schema: ENTRIES index ---

def test_init_db_creates_entries_index(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "IDX_ENTRIES_MATCH" in idx


def test_get_entries_for_event_is_chronological(tmp_path):
    # callers take [-1] as "latest"; ordering must not depend on physical row
    conn = db.init_db(str(tmp_path / "t.db"))
    for sold, ts in [(1, "2020-01-01 00:00:00"),
                     (2, "2021-01-01 00:00:00"),
                     (3, "2022-01-01 00:00:00")]:
        conn.execute(
            "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE, TIMESTAMP) "
            "VALUES ('m', ?, 1, ?)", (sold, ts))
    conn.commit()
    rows = db.get_entries_for_event(conn, "m")
    conn.close()
    assert [r[1] for r in rows] == [1, 2, 3]


# --- retention: prune_old_entries ---

def test_prune_old_entries_deletes_old_keeps_recent(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE, TIMESTAMP) "
        "VALUES ('old', 1, 1, '2020-01-01 00:00:00')")
    conn.execute(
        "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE, TIMESTAMP) "
        "VALUES ('new', 2, 2, datetime('now'))")
    conn.commit()
    deleted = db.prune_old_entries(conn, days=30)
    assert deleted == 1
    rows = conn.execute("SELECT match FROM ENTRIES ORDER BY TIMESTAMP").fetchall()
    conn.close()
    assert rows == [("new",)]


def test_prune_old_entries_preserves_events_metadata(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO EVENTS (ID, TITLE, DATETIME, SELLFROM, SELLTO) "
        "VALUES ('e', 't', '2020-01-01', '2020-01-01', '2020-01-02')")
    conn.execute(
        "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE, TIMESTAMP) "
        "VALUES ('e', 1, 1, '2020-01-01 00:00:00')")
    conn.commit()
    db.prune_old_entries(conn, days=1)
    events = conn.execute("SELECT id FROM EVENTS").fetchall()
    entries = conn.execute("SELECT match FROM ENTRIES").fetchall()
    conn.close()
    assert events == [("e",)]   # metadata kept
    assert entries == []        # sample pruned


def test_prune_old_entries_nothing_to_delete(tmp_path):
    conn = db.init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE, TIMESTAMP) "
        "VALUES ('m', 1, 1, datetime('now'))")
    conn.commit()
    assert db.prune_old_entries(conn, days=365) == 0
    conn.close()
