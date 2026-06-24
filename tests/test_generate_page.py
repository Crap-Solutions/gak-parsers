"""Tests for connection handling in ticket-fetch.py page generation.

generate_page must read through a single shared read-only connection, and
generate_mini_graph must use the caller's connection rather than opening one
per past-event card (N cards used to mean N connections).
"""
import datetime
from pathlib import Path


def _seed_future_event(mod, db_path, event_id="evt1", title="GAK 1902 : Rival"):
    """Create a DB with one future event and one sales entry."""
    conn = mod.db.init_db(str(db_path))
    conn.execute(
        "INSERT INTO EVENTS (ID,TITLE,DATETIME,SELLFROM,SELLTO) "
        "VALUES (?,?,?,?,?)",
        (event_id, title, "2099-01-01T20:00:00",
         "2099-01-01T00:00:00", "2099-01-01T20:00:00"))
    conn.execute(
        "INSERT INTO ENTRIES (MATCH,SOLD,AVAILABLE,TIMESTAMP) "
        "VALUES (?,?,?,?)",
        (event_id, 100, 50, "2099-01-01T10:00:00"))
    conn.commit()
    conn.close()


def test_generate_mini_graph_uses_passed_connection(ticket_fetch, tmp_path, monkeypatch):
    """generate_mini_graph queries the caller's connection; it must not open one."""
    db_path = tmp_path / "m.db"
    _seed_future_event(ticket_fetch, db_path)
    conn = ticket_fetch.db.open_connection(str(db_path), read_only=True)
    try:
        def boom(*a, **k):
            raise AssertionError("generate_mini_graph opened its own connection")
        monkeypatch.setattr(ticket_fetch.db, "init_db", boom)
        monkeypatch.setattr(ticket_fetch.db, "open_connection", boom)

        event_time = datetime.datetime(2099, 1, 1, 20, 0, 0)
        img = ticket_fetch.generate_mini_graph("evt1", event_time, conn)
        assert img is not None  # produced a graph via the passed connection
    finally:
        conn.close()


def test_generate_page_uses_single_readonly_connection(ticket_fetch, tmp_path, monkeypatch):
    """generate_page reads via exactly one read-only connection (no read-write)."""
    db_path = tmp_path / "p.db"
    _seed_future_event(ticket_fetch, db_path)

    # Stub graph.generate_graph so the only real connection is generate_page's own.
    monkeypatch.setattr(ticket_fetch.graph, "generate_graph", lambda path: "STUB")

    calls = {"init_db": 0, "open_ro": 0}
    real_init = ticket_fetch.db.init_db
    real_open = ticket_fetch.db.open_connection

    def spy_init(*a, **k):
        calls["init_db"] += 1
        return real_init(*a, **k)

    def spy_open(path, read_only=False, **k):
        if read_only:
            calls["open_ro"] += 1
        return real_open(path, read_only=read_only)

    monkeypatch.setattr(ticket_fetch.db, "init_db", spy_init)
    monkeypatch.setattr(ticket_fetch.db, "open_connection", spy_open)

    out = tmp_path / "index.html"
    templates = Path(ticket_fetch.__file__).parent / "templates"
    result = ticket_fetch.generate_page(db_path, out, str(templates))

    assert result is True
    assert calls["init_db"] == 0, "generate_page must not open a read-write connection"
    assert calls["open_ro"] == 1, \
        "generate_page must use exactly one read-only connection"


def test_capacity_percent_uses_raw_sold_not_offset(ticket_fetch, tmp_path, monkeypatch):
    """Capacity % is raw online-sold / stadium capacity, not the old
    unrecoverable offset estimate (latest[1] + 2333 + 285 + 296).

    For SOLD=7500 that is 50%; the removed offset formula gave 69%, so this
    also pins that the three invented attendance estimates stay out of the
    rendered page.
    """
    db_path = tmp_path / "cap.db"
    conn = ticket_fetch.db.init_db(str(db_path))
    conn.execute(
        "INSERT INTO EVENTS (ID,TITLE,DATETIME,SELLFROM,SELLTO) "
        "VALUES (?,?,?,?,?)",
        ("evt1", "GAK 1902 : Rival", "2099-01-01T20:00:00",
         "2099-01-01T00:00:00", "2099-01-01T20:00:00"))
    conn.execute(
        "INSERT INTO ENTRIES (MATCH,SOLD,AVAILABLE,TIMESTAMP) "
        "VALUES (?,?,?,?)",
        ("evt1", 7500, 1000, "2099-01-01T10:00:00"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(ticket_fetch.graph, "generate_graph", lambda path: "STUB")
    out = tmp_path / "index.html"
    templates = Path(ticket_fetch.__file__).parent / "templates"
    assert ticket_fetch.generate_page(db_path, out, str(templates)) is True

    html = out.read_text()
    # honest floor: 7500 / 15000 = 50% (the offset formula gave 69%)
    assert "Capacity (online sold): 50%" in html
    assert "Sold (online): 7500" in html
    # the three invented attendance estimates must be gone
    assert "w/o Sponsors, VIP" not in html
    assert "w est. Sponsors, VIP" not in html
