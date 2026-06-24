"""Tests for tickets/lib/graph.py connection handling.

generate_graph must close its read-only connection on *every* exit path,
including the early ``return None`` branches (no data) and exceptions.
Previously it only closed on the success path, leaking a connection.
"""
import sqlite3

import pytest

from lib import db, graph


def _track_open(monkeypatch, holder):
    """Capture the connection generate_graph opens so we can inspect it after."""
    real_open = db.open_connection

    def spy(path, read_only=False):
        conn = real_open(path, read_only=read_only)
        holder["conn"] = conn
        return conn

    monkeypatch.setattr(db, "open_connection", spy)


def _assert_closed(conn):
    """A closed sqlite3 connection raises ProgrammingError when used."""
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_generate_graph_closes_connection_on_empty(tmp_path, monkeypatch):
    """Empty DB -> no events -> return None, but the connection is still closed."""
    db.init_db(str(tmp_path / "g.db")).close()  # schema only, no rows

    holder = {}
    _track_open(monkeypatch, holder)

    assert graph.generate_graph(str(tmp_path / "g.db")) is None
    _assert_closed(holder["conn"])  # would still be usable if it leaked


def test_generate_graph_closes_connection_on_exception(tmp_path, monkeypatch):
    """If plotting raises after the connection opened, it is closed in finally."""
    conn = db.init_db(str(tmp_path / "g.db"))
    conn.execute(
        "INSERT INTO EVENTS (ID,TITLE,DATETIME,SELLFROM,SELLTO) "
        "VALUES (?,?,?,?,?)",
        ("evt1", "GAK 1902 : X", "2099-01-01T20:00:00+00:00",
         "2099-01-01T00:00:00+00:00", "2099-01-01T20:00:00+00:00"))
    conn.execute(
        "INSERT INTO ENTRIES (MATCH,SOLD,AVAILABLE,TIMESTAMP) "
        "VALUES (?,?,?,?)",
        ("evt1", 100, 50, "2099-01-01T10:00:00+00:00"))
    conn.commit()
    conn.close()

    holder = {}
    _track_open(monkeypatch, holder)
    # Force an error in the plotting stage, after the connection is opened.
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(graph.matplotlib.pyplot, "plot", _boom)

    assert graph.generate_graph(str(tmp_path / "g.db")) is None
    _assert_closed(holder["conn"])
