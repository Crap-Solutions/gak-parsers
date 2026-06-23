"""Tests for the alert-grace window and FetchError contract in ticket-fetch.

The upstream ticket server has regular short downtimes while cron polls every
5 minutes, so a failure must persist past a grace window before it emails.
These tests pin that behaviour and the api layer's raise-don't-log contract.
"""
import datetime
import logging
import sys

import pytest
import requests

import lib.api as api
from lib.api import FetchError


# --------------------------------------------------------------------------
# _resolve_alert_grace: flag > env > 24h default
# --------------------------------------------------------------------------

def test_grace_default_when_nothing_set(ticket_fetch, monkeypatch):
    monkeypatch.delenv("GAK_ALERT_GRACE", raising=False)
    assert ticket_fetch._resolve_alert_grace(None) == 24 * 60 * 60


def test_grace_env_used_when_no_flag(ticket_fetch, monkeypatch):
    monkeypatch.setenv("GAK_ALERT_GRACE", "3600")
    assert ticket_fetch._resolve_alert_grace(None) == 3600


def test_grace_flag_beats_env(ticket_fetch, monkeypatch):
    monkeypatch.setenv("GAK_ALERT_GRACE", "3600")
    assert ticket_fetch._resolve_alert_grace("120") == 120


def test_grace_zero_disables(ticket_fetch, monkeypatch):
    monkeypatch.delenv("GAK_ALERT_GRACE", raising=False)
    assert ticket_fetch._resolve_alert_grace("0") == 0


def test_grace_invalid_falls_back_to_default(ticket_fetch, monkeypatch, capsys):
    monkeypatch.delenv("GAK_ALERT_GRACE", raising=False)
    assert ticket_fetch._resolve_alert_grace("soon") == 24 * 60 * 60
    assert "invalid alert grace" in capsys.readouterr().err


def test_grace_negative_treated_as_disabled(ticket_fetch, monkeypatch, capsys):
    monkeypatch.delenv("GAK_ALERT_GRACE", raising=False)
    assert ticket_fetch._resolve_alert_grace("-5") == 0
    assert "negative alert grace" in capsys.readouterr().err


# --------------------------------------------------------------------------
# _failure_state_path
# --------------------------------------------------------------------------

def test_state_path_default_next_to_db(ticket_fetch, tmp_path):
    db = tmp_path / "data" / "events.db"
    assert ticket_fetch._failure_state_path(db) == tmp_path / "data" / "events.db.failstate"


def test_state_path_override_wins(ticket_fetch, tmp_path):
    db = tmp_path / "events.db"
    override = tmp_path / "elsewhere" / "state"
    assert ticket_fetch._failure_state_path(db, str(override)) == override


# --------------------------------------------------------------------------
# _within_grace
# --------------------------------------------------------------------------

def test_grace_disabled_returns_false_and_writes_nothing(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    assert ticket_fetch._within_grace(state, 0) is False
    assert ticket_fetch._within_grace(state, -10) is False
    assert not state.exists()  # disabled path must not touch the state file


def test_first_failure_stamps_state_and_is_within_grace(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    now = datetime.datetime.now(datetime.timezone.utc)
    assert ticket_fetch._within_grace(state, 3600, now=now) is True
    assert state.exists()
    # stamped timestamp round-trips and is the one we passed
    assert ticket_fetch._read_failure_since(state) == now


def test_subsequent_failure_within_window_still_grace(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(minutes=5)  # outage started 5 min ago
    state.write_text(since.isoformat(), encoding="utf-8")
    assert ticket_fetch._within_grace(state, 3600, now=now) is True
    # state file is not overwritten while still within grace
    assert ticket_fetch._read_failure_since(state) == since


def test_failure_past_window_alerts(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(hours=25)
    state.write_text(since.isoformat(), encoding="utf-8")
    assert ticket_fetch._within_grace(state, 24 * 3600, now=now) is False


def test_corrupt_state_treated_as_fresh_outage(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    state.write_text("not a timestamp", encoding="utf-8")
    now = datetime.datetime.now(datetime.timezone.utc)
    # garbage -> treated as no prior outage -> new outage -> within grace
    assert ticket_fetch._within_grace(state, 3600, now=now) is True
    assert ticket_fetch._read_failure_since(state) == now  # overwritten cleanly


def test_unwritable_state_fails_safe_to_alert(ticket_fetch, tmp_path):
    # parent path is a regular file -> cannot create state file
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    state = blocker / "state"
    assert ticket_fetch._within_grace(state, 3600) is False


# --------------------------------------------------------------------------
# _clear_failure_state
# --------------------------------------------------------------------------

def test_clear_failure_state_removes_file(ticket_fetch, tmp_path):
    state = tmp_path / "s"
    state.write_text("x")
    ticket_fetch._clear_failure_state(state)
    assert not state.exists()


def test_clear_failure_state_missing_is_fine(ticket_fetch, tmp_path):
    state = tmp_path / "missing"
    # must not raise
    ticket_fetch._clear_failure_state(state)


# --------------------------------------------------------------------------
# _log_to_file_only: file handler gets it, stdout handler does not
# --------------------------------------------------------------------------

def test_log_to_file_only_skips_stdout_handler(ticket_fetch, tmp_path):
    log = ticket_fetch.logger
    saved = log.handlers
    try:
        import io
        buf_stream = io.StringIO()
        stdout_h = logging.StreamHandler(buf_stream)
        stdout_h.setLevel(logging.INFO)
        file_h = logging.FileHandler(str(tmp_path / "f.log"))
        file_h.setLevel(logging.INFO)
        log.handlers = [stdout_h, file_h]

        ticket_fetch._log_to_file_only(logging.ERROR, "SECRET_FAILURE")

        assert "SECRET_FAILURE" not in buf_stream.getvalue()  # stdout untouched
        assert "SECRET_FAILURE" in (tmp_path / "f.log").read_text()  # file has it
    finally:
        log.handlers = saved


# --------------------------------------------------------------------------
# api: fetch_events / fetch_event_details raise FetchError, don't log to stdout
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status=200, json_data=None, json_exc=None):
        self.status_code = status
        self._json_data = json_data
        self._json_exc = json_exc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Server Error")

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


def _patch_get(monkeypatch, response=None, exc=None):
    def fake_get(*args, **kwargs):
        if exc is not None:
            raise exc
        return response
    monkeypatch.setattr(api.requests, "get", fake_get)


def test_fetch_events_success_returns_list(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(json_data=[{"id": "a"}]))
    assert api.fetch_events("https://x/", "ep") == [{"id": "a"}]


def test_fetch_events_empty_list_is_success_not_error(monkeypatch):
    # genuinely empty result must NOT raise (server is up, just no events)
    _patch_get(monkeypatch, response=_FakeResponse(json_data=[]))
    assert api.fetch_events("https://x/", "ep") == []


def test_fetch_events_502_raises_fetcherror(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(status=502))
    with pytest.raises(FetchError) as ei:
        api.fetch_events("https://x/", "ep")
    assert "Failed to fetch events" in str(ei.value)
    assert "502" in str(ei.value)


def test_fetch_events_timeout_raises_fetcherror(monkeypatch):
    _patch_get(monkeypatch, exc=requests.exceptions.Timeout("slow"))
    with pytest.raises(FetchError, match="Timeout fetching events"):
        api.fetch_events("https://x/", "ep")


def test_fetch_events_bad_json_raises_fetcherror(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(json_exc=ValueError("nope")))
    with pytest.raises(FetchError, match="Invalid JSON response"):
        api.fetch_events("https://x/", "ep")


def test_fetch_events_non_list_raises_fetcherror(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(json_data={"not": "a list"}))
    with pytest.raises(FetchError, match="Expected list"):
        api.fetch_events("https://x/", "ep")


def test_fetch_event_details_success(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(json_data={"ok": True}))
    assert api.fetch_event_details("https://x/", "evt") == {"ok": True}


def test_fetch_event_details_502_raises(monkeypatch):
    _patch_get(monkeypatch, response=_FakeResponse(status=502))
    with pytest.raises(FetchError) as ei:
        api.fetch_event_details("https://x/", "evt")
    assert "evt" in str(ei.value)


def test_fetch_events_does_not_log_to_stdout_on_failure(monkeypatch, caplog):
    # the api layer must not alert on its own; main() owns alerting
    _patch_get(monkeypatch, response=_FakeResponse(status=502))
    with caplog.at_level(logging.DEBUG, logger="lib.api"):
        with pytest.raises(FetchError):
            api.fetch_events("https://x/", "ep")
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


# --------------------------------------------------------------------------
# main(): grace wiring (side effects, not stdout capture)
# --------------------------------------------------------------------------

def _run_main(ticket_fetch, monkeypatch, tmp_path, argv, fetch_impl):
    monkeypatch.setattr(ticket_fetch.api, "fetch_events", fetch_impl)
    monkeypatch.setattr(sys, "argv", ["ticket-fetch.py"] + argv)
    monkeypatch.delenv("GAK_ALERT_GRACE", raising=False)
    try:
        ticket_fetch.main()
    except SystemExit as ei:
        return ei.code
    return 0


def test_main_first_failure_suppressed_preserves_page_and_stamps_state(
        ticket_fetch, monkeypatch, tmp_path):
    db = tmp_path / "events.db"
    out = tmp_path / "index.html"
    out.write_text("GOOD PAGE", encoding="utf-8")
    state = ticket_fetch._failure_state_path(db)

    def boom(*a, **k):
        raise ticket_fetch.api.FetchError("502 Bad Gateway")

    code = _run_main(ticket_fetch, monkeypatch, tmp_path,
                     ["--db", str(db), "--output", str(out), "--generate",
                      "--log", str(tmp_path / "fetch.log")], boom)
    assert code == 0                               # graceful, no alert
    assert out.read_text() == "GOOD PAGE"          # last good page preserved
    assert state.exists()                          # outage start recorded


def test_main_sustained_failure_alerts_and_writes_error_page(
        ticket_fetch, monkeypatch, tmp_path):
    db = tmp_path / "events.db"
    out = tmp_path / "index.html"
    out.write_text("GOOD PAGE", encoding="utf-8")
    state = ticket_fetch._failure_state_path(db)
    # outage started 25h ago -> past the 24h default grace
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=25)
    state.write_text(since.isoformat(), encoding="utf-8")

    def boom(*a, **k):
        raise ticket_fetch.api.FetchError("502 Bad Gateway")

    code = _run_main(ticket_fetch, monkeypatch, tmp_path,
                     ["--db", str(db), "--output", str(out), "--generate",
                      "--log", str(tmp_path / "fetch.log")], boom)
    assert code == 1                               # real alert
    page = out.read_text()
    assert "GOOD PAGE" not in page                 # page replaced
    assert "Error" in page or "ERROR" in page      # with the error page


def test_main_successful_fetch_clears_failure_state(
        ticket_fetch, monkeypatch, tmp_path):
    db = tmp_path / "events.db"
    out = tmp_path / "index.html"
    state = ticket_fetch._failure_state_path(db)
    state.write_text(
        (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(hours=1)).isoformat(),
        encoding="utf-8")

    # server back up, but no events -> exits 0 after clearing state
    code = _run_main(ticket_fetch, monkeypatch, tmp_path,
                     ["--db", str(db), "--output", str(out),
                      "--log", str(tmp_path / "fetch.log")],
                     lambda *a, **k: [])
    assert code == 0
    assert not state.exists()                       # outage state cleared
