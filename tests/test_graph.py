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


def test_generate_graph_closes_figure_on_exception(tmp_path, monkeypatch):
    """A plotting error must not leak the matplotlib figure.

    generate_graph creates the figure before plotting; previously the figure
    was only closed on the success path, so an exception after figure creation
    left it open for the rest of the cron run."""
    import matplotlib.pyplot as plt
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

    plt.close("all")  # baseline: no leftover figures from earlier tests

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(graph.matplotlib.pyplot, "plot", _boom)

    assert graph.generate_graph(str(tmp_path / "g.db")) is None
    assert plt.get_fignums() == [], f"leaked figures: {plt.get_fignums()}"


def test_average_line_uses_past_events_only(tmp_path, monkeypatch):
    """The 'Average' line must average final sales of past events only.

    get_events_for_graph deliberately includes future events (their sales
    curves are plotted), but their tallies are still climbing -- averaging
    them in drags the line down. Seed one past event (final sales 100) and
    one future event (partial tally 900): the average must be 100, not 500.
    """
    conn = db.init_db(str(tmp_path / "g.db"))
    for event_id, title, when in (
            ("evt_past", "GAK 1902 : Past", "2020-01-01T20:00:00+00:00"),
            ("evt_future", "GAK 1902 : Future", "2099-01-01T20:00:00+00:00")):
        conn.execute(
            "INSERT INTO EVENTS (ID,TITLE,DATETIME,SELLFROM,SELLTO) "
            "VALUES (?,?,?,?,?)",
            (event_id, title, when, when, when))
    for event_id, samples in (
            ("evt_past", (("2020-01-01T10:00:00+00:00", 10),
                          ("2020-01-01T18:00:00+00:00", 100))),
            ("evt_future", (("2098-12-31T10:00:00+00:00", 500),
                            ("2099-01-01T10:00:00+00:00", 900)))):
        for ts, sold in samples:
            conn.execute(
                "INSERT INTO ENTRIES (MATCH,SOLD,AVAILABLE,TIMESTAMP) "
                "VALUES (?,?,?,?)", (event_id, sold, 0, ts))
    conn.commit()
    conn.close()

    seen = {}

    def record_axhline(y=0, **kwargs):
        seen["y"] = y

    monkeypatch.setattr(graph.matplotlib.pyplot, "axhline", record_axhline)

    img = graph.generate_graph(str(tmp_path / "g.db"))
    assert img is not None, "graph should still render with both events"
    assert seen.get("y") == 100, \
        f"average must use past events' final sales only, got {seen.get('y')}"
