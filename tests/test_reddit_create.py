"""Tests for reddit/reddit-create.py error handling and parsing logic."""
from unittest import mock

import requests


# --- get_table ---

def test_get_table_success(reddit_create):
    payload = [
        {"teamName": "GAK 1902", "points": 10},
        {"teamName": "Sturm", "points": 3},
    ]
    resp = mock.Mock()
    resp.json.return_value = payload
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        table = reddit_create.get_table("http://x")
    assert table == [
        {"name": "GAK 1902", "points": 10},
        {"name": "Sturm", "points": 3},
    ]


def test_get_table_timeout_returns_none(reddit_create):
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=requests.exceptions.Timeout("slow")):
        assert reddit_create.get_table("http://x") is None


def test_get_table_bad_json_returns_none(reddit_create):
    resp = mock.Mock()
    resp.json.side_effect = ValueError("not json")
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_table("http://x") is None


def test_get_table_request_error_returns_none(reddit_create):
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=requests.exceptions.ConnectionError("down")):
        assert reddit_create.get_table("http://x") is None


def test_get_table_wrong_shape_returns_none(reddit_create):
    resp = mock.Mock()
    resp.json.return_value = {"unexpected": True}  # not a list
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_table("http://x") is None


def test_get_table_skips_malformed_entries(reddit_create):
    payload = [
        {"teamName": "GAK 1902", "points": 10},
        {"points": 5},                 # missing teamName -> skipped
        "not a dict",                  # skipped
        {"teamName": "Sturm", "points": 7},
    ]
    resp = mock.Mock()
    resp.json.return_value = payload
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        table = reddit_create.get_table("http://x")
    assert table == [
        {"name": "GAK 1902", "points": 10},
        {"name": "Sturm", "points": 7},
    ]


# --- get_gameplan ---

def _gameplan_payload():
    return {
        "league": [{"league": "1"}],
        "all": [
            {"datum": "01.01.2026", "uhrzeit": "17:00", "heim": "GAK 1902",
             "gast": "Rival", "heimTore": 2, "gastTore": 1, "league": "1"},
            {"datum": "08.01.2026", "uhrzeit": "17:00", "heim": "Other",
             "gast": "GAK 1902", "heimTore": None, "gastTore": None, "league": "1"},
        ],
    }


def test_get_gameplan_success(reddit_create):
    resp = mock.Mock()
    resp.json.return_value = _gameplan_payload()
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        gp = reddit_create.get_gameplan("http://x")
    assert len(gp) == 2
    # finished GAK home win -> "W (2:1)"
    assert gp[0]["res"] == "W (2:1)"
    assert gp[0]["league"] is True
    # no result yet -> "-:-"
    assert gp[1]["res"] == "-:-"


def test_get_gameplan_missing_all_returns_none(reddit_create):
    resp = mock.Mock()
    resp.json.return_value = {"league": []}
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_gameplan("http://x") is None


def test_get_gameplan_not_a_dict_returns_none(reddit_create):
    resp = mock.Mock()
    resp.json.return_value = ["unexpected"]
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_gameplan("http://x") is None


def test_get_gameplan_timeout_returns_none(reddit_create):
    with mock.patch.object(reddit_create.requests, "get",
                           side_effect=requests.exceptions.Timeout()):
        assert reddit_create.get_gameplan("http://x") is None


# --- get_next_games ---

def _freeze_now(mod, when):
    """Replace mod.datetime with a subclass whose now() returns a fixed value."""
    real = mod.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(when.year, when.month, when.day)

    return mock.patch.object(mod, "datetime", Frozen)


def test_get_next_games_returns_future_only(reddit_create):
    gp = [
        {"date": "01.01.2000"},   # past
        {"date": "01.01.2099"},   # future
        {"date": "02.01.2099"},   # future
        {"date": "03.01.2099"},   # beyond the 2-game limit
    ]
    with _freeze_now(reddit_create, reddit_create.datetime(2026, 6, 20)):
        nxt = reddit_create.get_next_games(gp)
    assert [e["date"] for e in nxt] == ["01.01.2099", "02.01.2099"]


def test_get_next_games_skips_bad_date(reddit_create):
    gp = [{"date": "not-a-date"}, {"date": "01.01.2099"}]
    with _freeze_now(reddit_create, reddit_create.datetime(2026, 6, 20)):
        nxt = reddit_create.get_next_games(gp)
    assert [e["date"] for e in nxt] == ["01.01.2099"]


def test_get_next_games_respects_limit(reddit_create):
    gp = [{"date": f"0{d}.01.2099"} for d in range(1, 6)]
    with _freeze_now(reddit_create, reddit_create.datetime(2026, 6, 20)):
        assert len(reddit_create.get_next_games(gp, limit=3)) == 3


# --- get_gp_title ---

def test_get_gp_title(reddit_create):
    gp = [{"date": "01.09.2025"}, {"date": "30.06.2026"}]
    assert reddit_create.get_gp_title(gp) == "Spielplan 2025/26"


def test_get_gameplan_empty_league_returns_none(reddit_create):
    """An empty 'league' list used to reach dataset['league'][0] and raise
    IndexError mid-loop, aborting the run. It must be treated as invalid."""
    resp = mock.Mock()
    resp.json.return_value = {
        "league": [],
        "all": [
            {"datum": "01.01.2026", "uhrzeit": "17:00", "heim": "GAK 1902",
             "gast": "Rival", "heimTore": None, "gastTore": None,
             "league": "1"}],
    }
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_gameplan("http://x") is None


def test_get_gameplan_missing_league_returns_none(reddit_create):
    """A response with no 'league' key at all is likewise invalid."""
    resp = mock.Mock()
    resp.json.return_value = {"all": []}
    with mock.patch.object(reddit_create.requests, "get", return_value=resp):
        assert reddit_create.get_gameplan("http://x") is None
