"""Tests for the quiet-window gameplan outage alerting in reddit-create.py.

Policy: a failed gameplan fetch is silent (exit 0, no stdout at cron's
WARNING level) until the outage has lasted 24h, then alerts once and at
most once per further 24h. Success clears the state. Corrupt/unwritable
state fails safe (alert).
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _read_state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _stamp(path, since, notified=None):
    path.write_text(json.dumps({
        "since": since.isoformat(),
        "notified": notified.isoformat() if notified else None,
    }), encoding="utf-8")


# --- record_gameplan_failure ---

def test_first_failure_is_quiet_and_stamps_outage_start(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    alert, since = reddit_create.record_gameplan_failure(state, now=NOW)
    assert alert is False
    assert since == NOW
    data = _read_state(state)
    assert datetime.fromisoformat(data["since"]) == NOW
    assert data["notified"] is None


def test_failure_within_24h_stays_quiet_and_preserves_start(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    reddit_create.record_gameplan_failure(state, now=NOW)
    later = NOW + timedelta(hours=23, minutes=59)
    alert, since = reddit_create.record_gameplan_failure(state, now=later)
    assert alert is False
    assert since == NOW
    assert datetime.fromisoformat(_read_state(state)["since"]) == NOW


def test_failure_at_24h_alerts_and_stamps_notified(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    reddit_create.record_gameplan_failure(state, now=NOW)
    later = NOW + timedelta(hours=24)
    alert, since = reddit_create.record_gameplan_failure(state, now=later)
    assert alert is True
    assert since == NOW
    data = _read_state(state)
    assert datetime.fromisoformat(data["since"]) == NOW
    assert datetime.fromisoformat(data["notified"]) == later


def test_no_realert_within_repeat_window(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    notified = NOW + timedelta(hours=24)
    _stamp(state, since=NOW, notified=notified)
    alert, _ = reddit_create.record_gameplan_failure(
        state, now=notified + timedelta(hours=23, minutes=59))
    assert alert is False


def test_realerts_after_repeat_window(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    notified = NOW + timedelta(hours=24)
    _stamp(state, since=NOW, notified=notified)
    alert, since = reddit_create.record_gameplan_failure(
        state, now=notified + timedelta(hours=24))
    assert alert is True
    assert since == NOW
    assert datetime.fromisoformat(_read_state(state)["notified"]) == \
        notified + timedelta(hours=24)


def test_corrupt_state_fails_safe_and_alerts(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    state.write_text("{not json", encoding="utf-8")
    alert, since = reddit_create.record_gameplan_failure(state, now=NOW)
    assert alert is True
    assert since is None
    # rewritten to a sane, alerted state
    data = _read_state(state)
    assert datetime.fromisoformat(data["since"]) == NOW
    assert datetime.fromisoformat(data["notified"]) == NOW


def test_state_with_missing_keys_fails_safe(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    state.write_text("{}", encoding="utf-8")  # no 'since' key -> KeyError
    alert, since = reddit_create.record_gameplan_failure(state, now=NOW)
    assert alert is True
    assert since is None


def test_unwritable_state_fails_safe(reddit_create, tmp_path):
    blocker = tmp_path / "sub"
    blocker.write_text("occupied", encoding="utf-8")  # 'sub' is a file
    state = blocker / "gameplan.failstate"            # ... so mkdir fails
    alert, since = reddit_create.record_gameplan_failure(state, now=NOW)
    assert alert is True
    assert since is None


def test_naive_timestamps_treated_as_utc(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    _stamp(state, since=datetime(2026, 9, 12, 11, 0))  # naive, ~25h before NOW
    alert, since = reddit_create.record_gameplan_failure(state, now=NOW)
    assert alert is True
    assert since == datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)


# --- clear_gameplan_failure ---

def test_clear_removes_state_and_returns_duration(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    reddit_create.record_gameplan_failure(state, now=NOW)
    duration = reddit_create.clear_gameplan_failure(
        state, now=NOW + timedelta(hours=5))
    assert duration == timedelta(hours=5)
    assert not state.exists()


def test_clear_without_state_returns_none(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    assert reddit_create.clear_gameplan_failure(state, now=NOW) is None


def test_clear_removes_corrupt_state(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    state.write_text("garbage", encoding="utf-8")
    assert reddit_create.clear_gameplan_failure(state, now=NOW) is None
    assert not state.exists()


# --- run() wiring ---

def _run_args(state_path):
    return argparse.Namespace(timeout=30, gameplan_state=str(state_path))


def _mock_responses(table_payload, gameplan_payload):
    table_resp = mock.Mock()
    table_resp.json.return_value = table_payload
    gp_resp = mock.Mock()
    gp_resp.json.return_value = gameplan_payload

    def fake_get(url, timeout=None):
        return table_resp if "table" in url else gp_resp

    return fake_get


def test_run_quiet_exit_0_on_fresh_gameplan_outage(reddit_create, tmp_path):
    """A young outage must not print ERROR (cron mail) nor exit 1."""
    state = tmp_path / "gameplan.failstate"
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=_mock_responses(
                               [{"teamName": "GAK 1902", "points": 7}], {})), \
         mock.patch.object(reddit_create.praw, "Reddit"), \
         mock.patch.object(reddit_create, "logger") as log:
        rc = reddit_create.run(_run_args(state))
    assert rc == 0
    assert state.exists()  # outage recorded
    log.error.assert_not_called()  # nothing that would trigger a cron mail


def test_run_alerts_after_24h_outage(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    _stamp(state, since=NOW - timedelta(hours=25))
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=_mock_responses(
                               [{"teamName": "GAK 1902", "points": 7}], {})), \
         mock.patch.object(reddit_create.praw, "Reddit"), \
         mock.patch.object(reddit_create, "logger") as log:
        rc = reddit_create.run(_run_args(state))
    assert rc == 1
    assert any("Game plan unavailable" in str(call)
               for call in log.error.call_args_list)


def test_run_success_clears_outage_state(reddit_create, tmp_path):
    state = tmp_path / "gameplan.failstate"
    _stamp(state, since=NOW - timedelta(hours=2))
    payload = {"league": [{"league": "1"}],
               "all": [{"datum": "01.01.2026", "uhrzeit": "17:00",
                        "heim": "GAK 1902", "gast": "Rival",
                        "heimTore": 2, "gastTore": 1, "league": "1"}]}
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=_mock_responses(
                               [{"teamName": "GAK 1902", "points": 7}], payload)), \
         mock.patch.object(reddit_create.praw, "Reddit"):
        reddit_create.run(_run_args(state))
    assert not state.exists()


def test_run_table_failure_still_alerts_immediately(reddit_create, tmp_path):
    """Table outages keep the old behaviour: ERROR + exit 1 on first miss."""
    state = tmp_path / "gameplan.failstate"
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=requests.exceptions.Timeout("down")), \
         mock.patch.object(reddit_create.praw, "Reddit"), \
         mock.patch.object(reddit_create, "logger") as log:
        rc = reddit_create.run(_run_args(state))
    assert rc == 1
    assert any("Aborting" in str(call) for call in log.error.call_args_list)
