"""Tests for the shared logging setup (gak_common.log) and each cron script's
wiring of it.

The key contract for cron users: setting the level to WARNING must silence
INFO on stdout (so successful cron runs produce no output -> no mail) while
the file log still captures INFO for debugging.
"""
import io
import logging

import pytest

import gak_common.log as gak_log


# --- resolve_log_level: precedence (flag > env > default) and parsing ---
# Logic lives once in gak_common.log now; the per-script wiring is exercised
# by test_warning_level_silences_stdout_but_keeps_file below.

def test_resolve_level_default(monkeypatch):
    monkeypatch.delenv("GAK_LOG_LEVEL", raising=False)
    assert gak_log.resolve_log_level(None) == logging.INFO


def test_resolve_level_env_used_when_no_flag(monkeypatch):
    monkeypatch.setenv("GAK_LOG_LEVEL", "WARNING")
    assert gak_log.resolve_log_level(None) == logging.WARNING


def test_resolve_level_flag_beats_env(monkeypatch):
    monkeypatch.setenv("GAK_LOG_LEVEL", "ERROR")
    assert gak_log.resolve_log_level("DEBUG") == logging.DEBUG


@pytest.mark.parametrize("name,expected", [
    ("DEBUG", logging.DEBUG),
    ("INFO", logging.INFO),
    ("WARNING", logging.WARNING),
    ("ERROR", logging.ERROR),
    ("CRITICAL", logging.CRITICAL),
    ("warning", logging.WARNING),   # case-insensitive
    ("20", logging.INFO),           # numeric
    ("30", logging.WARNING),
])
def test_resolve_level_named_and_numeric(name, expected):
    assert gak_log.resolve_log_level(name) == expected


def test_resolve_level_invalid_falls_back_to_info(capsys):
    assert gak_log.resolve_log_level("NOPE") == logging.INFO
    assert "invalid log level" in capsys.readouterr().err


# --- contract: WARNING suppresses INFO on stdout, keeps it in the file ---
# Per-script: reddit/tippspiel call the shared setup_logging; ticket-fetch
# wires its handlers inline in main() (it has bespoke file-log/grace handling).
# Each must still honour the WARNING-silences-stdout contract.

def _capture_stdout_handler(modobj):
    """Return the StreamHandler that writes to stdout (not a FileHandler)."""
    for h in list(logging.getLogger().handlers) + list(modobj.logger.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            return h
    raise AssertionError("no stdout StreamHandler found")


@pytest.mark.parametrize("setup", ["reddit", "tippspiel", "ticket-fetch"])
def test_warning_level_silences_stdout_but_keeps_file(setup, request, tmp_path, monkeypatch):
    monkeypatch.delenv("GAK_LOG_LEVEL", raising=False)
    # reset root + module loggers for a clean slate
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    buf = io.StringIO()

    if setup == "reddit":
        m = request.getfixturevalue("reddit_create")
        m.setup_logging(str(tmp_path / "r.log"), stdout_level=m.resolve_log_level("WARNING"))
    elif setup == "tippspiel":
        m = request.getfixturevalue("tippspiel_table")
        m.setup_logging(str(tmp_path / "t.log"), stdout_level=m.resolve_log_level("WARNING"))
    else:
        # ticket-fetch applies the level to existing stdout StreamHandlers
        # in main(); mirror that here with a buffer-backed handler so we can
        # inspect output regardless of whether basicConfig already ran.
        m = request.getfixturevalue("ticket_fetch")
        logging.getLogger().setLevel(logging.INFO)
        stdout_handler = logging.StreamHandler(buf)
        logging.getLogger().addHandler(stdout_handler)
        lvl = m.resolve_log_level("WARNING")
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(lvl)
        fh = logging.FileHandler(str(tmp_path / "f.log"))
        fh.setFormatter(logging.Formatter("%(message)s"))
        m.logger.addHandler(fh)

    logfile = tmp_path / ("r.log" if setup == "reddit" else "t.log" if setup == "tippspiel" else "f.log")

    # For reddit/tippspiel, setup_logging created a stdout handler pointing at
    # the real sys.stdout; retarget it to our buffer. (ticket-fetch already
    # writes to buf.)
    if setup != "ticket-fetch":
        sh = _capture_stdout_handler(m)
        sh.stream = buf

    m.logger.info("AN_INFO_LINE")
    m.logger.error("AN_ERROR_LINE")

    stdout = buf.getvalue()
    filetext = logfile.read_text()
    assert "AN_INFO_LINE" not in stdout, "INFO leaked to stdout at WARNING level"
    assert "AN_ERROR_LINE" in stdout, "ERROR missing from stdout"
    assert "AN_INFO_LINE" in filetext, "INFO missing from file log"
    assert "AN_ERROR_LINE" in filetext, "ERROR missing from file log"
