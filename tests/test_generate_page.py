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


def _seed_future_event_with_entries(mod, db_path, entries):
    """Create a DB with one future event and the given ENTRIES rows.

    `entries` is a list of (sold, available, timestamp) tuples so the caller
    controls exactly which timestamps (and tz offsets) the velocity logic sees.
    """
    conn = mod.db.init_db(str(db_path))
    conn.execute(
        "INSERT INTO EVENTS (ID,TITLE,DATETIME,SELLFROM,SELLTO) "
        "VALUES (?,?,?,?,?)",
        ("evt1", "GAK 1902 : Rival", "2099-01-01T20:00:00",
         "2099-01-01T00:00:00", "2099-01-01T20:00:00"))
    for sold, avail, ts in entries:
        conn.execute(
            "INSERT INTO ENTRIES (MATCH,SOLD,AVAILABLE,TIMESTAMP) "
            "VALUES (?,?,?,?)",
            ("evt1", sold, avail, ts))
    conn.commit()
    conn.close()


def test_velocity_window_normalises_offset_timestamps(ticket_fetch, tmp_path, monkeypatch):
    """Velocity windows compare against a UTC-naive `now`, so offset timestamps
    must be converted to UTC before the naive strip -- not stripped in place.

    All three samples are stored with timestamps that, in real UTC time, fall
    *before* the 10-minute window (so the correct velocity is 0). But samples
    B and C carry a +02:00 offset; stripping that naively moves their wall-clock
    to 11:53 / 11:54, which is >= window_ago (09:55) and so they'd be wrongly
    included, yielding a spurious "100 in last 10min". The fix removes that.
    """
    import datetime as _dt
    db_path = tmp_path / "v.db"
    fixed_now = _dt.datetime(2099, 1, 1, 10, 5, 0)  # 10:05 UTC-naive
    # ticket-fetch does `import datetime` (module) and calls
    # datetime.datetime.now(...). Freeze both the naive and tz-aware forms.
    real_datetime_cls = ticket_fetch.datetime.datetime

    class Frozen(real_datetime_cls):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return real_datetime_cls(2099, 1, 1, 10, 5, 0, tzinfo=tz)
            return fixed_now

    monkeypatch.setattr(ticket_fetch.datetime, "datetime", Frozen)
    # now=10:05 -> 10-min window_ago = 09:55. Every sample is before 09:55 in
    # real UTC time, so none belongs in the window.
    _seed_future_event_with_entries(ticket_fetch, db_path, [
        (100, 50, "2099-01-01T09:50:00+00:00"),  # 09:50 UTC: before window
        (200, 0,  "2099-01-01T11:53:00+02:00"),  # == 09:53 UTC: before window
        (300, 0,  "2099-01-01T11:54:00+02:00"),  # == 09:54 UTC: before window
    ])
    out = tmp_path / "index.html"
    templates = Path(ticket_fetch.__file__).parent / "templates"
    monkeypatch.setattr(ticket_fetch.graph, "generate_graph", lambda path: "STUB")
    result = ticket_fetch.generate_page(db_path, out, str(templates))
    assert result is True
    html = out.read_text(encoding="utf-8")
    # All three samples are >10 min before `now` in real UTC time, so the
    # 10-minute window must be empty -> no "... in last 10min" term. (The
    # 1-hour / 1-day windows are wider and legitimately non-empty here, so
    # we assert specifically on the 10-min term.)
    assert "in last 10min" not in html, \
        f"spurious 10-min velocity from mis-compared offset timestamps: {html}"
