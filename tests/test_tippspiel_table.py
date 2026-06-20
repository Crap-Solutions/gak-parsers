"""Tests for tippspiel/tippspiel-table.py error handling and scoring logic."""
from unittest import mock

import pytest


# --- parse_data ---

def test_parse_data_success(tippspiel_table):
    sdata = [[
        ["hdr", "h1", "h2", "h3", "hscore", "hobg"],   # s[0] header, skipped
        ["alice", "x", "y", "z", "1SC", "1"],          # e[0]=alice, e[4]=1SC, e[5]=1
        ["bob", "x", "y", "z", "W", "2"],
    ]]
    out = tippspiel_table.parse_data(sdata)
    assert out == [{"alice": {"score": "1SC", "obg": "1"},
                    "bob": {"score": "W", "obg": "2"}}]


def test_parse_data_skips_malformed_row(tippspiel_table):
    sdata = [[
        ["hdr"],
        ["alice", "x", "y", "z", "1SC", "1"],
        ["short"],                 # IndexError on e[4] -> skipped
        ["bob", "x", "y", "z", "FSC", "3"],
    ]]
    out = tippspiel_table.parse_data(sdata)
    assert out == [{"alice": {"score": "1SC", "obg": "1"},
                    "bob": {"score": "FSC", "obg": "3"}}]


def test_parse_data_skips_non_list_row(tippspiel_table):
    sdata = [[
        ["hdr"],
        42,                          # int is not subscriptable -> TypeError, skipped
        ["alice", "x", "y", "z", "W", "5"],
    ]]
    out = tippspiel_table.parse_data(sdata)
    assert out == [{"alice": {"score": "W", "obg": "5"}}]


# --- get_players ---

def test_get_players(tippspiel_table):
    results = [{"a": 1, "b": 2}, {"b": 2, "c": 3}]
    assert set(tippspiel_table.get_players(results)) == {"a", "b", "c"}


# --- get_table_data (scoring weights + ordering) ---
# weights: 1SC=1, FSC=2, 2SC=3, FSC1=6, W=12

def test_get_table_data_scoring_and_sort(tippspiel_table):
    results = [{
        "alice": {"score": "W", "obg": "1"},     # 1 x W  = 12
        "bob": {"score": "1SC", "obg": "0"},     # 1 x 1SC = 1
    }, {
        "bob": {"score": "1SC", "obg": "0"},     # + 1SC  -> 2 total
    }]
    table = tippspiel_table.get_table_data(results)
    # alice (12) ranks above bob (2)
    assert table[0][0] == "alice"
    assert table[0][-1] == 12
    assert table[1][0] == "bob"
    assert table[1][-1] == 2


def test_get_table_data_weight_matrix(tippspiel_table):
    # one player per distinct score category
    results = [{
        "p1SC": {"score": "1SC", "obg": "0"},
        "pFSC": {"score": "FSC", "obg": "0"},
        "p2SC": {"score": "2SC", "obg": "0"},
        "pFSC1": {"score": "FSC1", "obg": "0"},
        "pW": {"score": "W", "obg": "0"},
    }]
    table = {row[0]: row[-1] for row in tippspiel_table.get_table_data(results)}
    assert table == {"p1SC": 1, "pFSC": 2, "p2SC": 3, "pFSC1": 6, "pW": 12}


# --- load_credentials: non-interactive hang prevention ---

def test_load_credentials_non_interactive_exits(tippspiel_table, tmp_path, monkeypatch):
    """With no stored token and no TTY, must exit(2) instead of launching OAuth."""
    monkeypatch.setattr(tippspiel_table, "CONFIG_DIR", tmp_path)  # no token.json here
    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = False
    fake_stdout = mock.Mock()
    fake_stdout.isatty.return_value = False
    monkeypatch.setattr(tippspiel_table.sys, "stdin", fake_stdin)
    monkeypatch.setattr(tippspiel_table.sys, "stdout", fake_stdout)

    with pytest.raises(SystemExit) as exc:
        tippspiel_table.load_credentials()
    assert exc.value.code == 2
