"""Tests for tickets/ticket-fetch.py log-file resolution fallback.

The live ticket-fetch cron job defaults its log to /var/log/gak-ticket.log,
which is only writable by root. When that is unavailable (the common
non-root cron case) it must fall back to a writable location rather than
silently dropping all logs.
"""


def test_resolve_log_file_first_writable(ticket_fetch, tmp_path):
    a = tmp_path / "a.log"
    b = tmp_path / "b.log"
    assert ticket_fetch._resolve_log_file([str(a), str(b)]) == str(a)


def test_resolve_log_file_falls_back_to_second(ticket_fetch, tmp_path):
    # First candidate's parent is a regular file -> cannot be created -> skip.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    bad = blocker / "x.log"
    good = tmp_path / "good.log"
    assert ticket_fetch._resolve_log_file([str(bad), str(good)]) == str(good)


def test_resolve_log_file_creates_parent_directory(ticket_fetch, tmp_path):
    nested = tmp_path / "deep" / "nested" / "dir" / "ticket-fetch.log"
    assert ticket_fetch._resolve_log_file([str(nested)]) == str(nested)
    assert nested.parent.exists()


def test_resolve_log_file_none_writable_returns_none(ticket_fetch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file")  # parent is a file for both candidates
    bad1 = blocker / "x.log"
    bad2 = blocker / "y.log"
    assert ticket_fetch._resolve_log_file([str(bad1), str(bad2)]) is None
