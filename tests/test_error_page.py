"""Tests for the public error/empty HTML pages in ticket-fetch.py.

These pages are served from /var/www and their text frequently originates
from upstream API errors or exception messages, so any interpolated value
must be HTML-escaped to prevent markup/script injection.
"""


def test_error_html_escapes_message(ticket_fetch):
    out = ticket_fetch.generate_error_html("<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_error_html_escapes_last_successful_run(ticket_fetch):
    out = ticket_fetch.generate_error_html("ok", last_successful_run="<b>x</b>")
    # the raw payload must not survive, its escaped form must
    assert "<b>x</b>" not in out
    assert "&lt;b&gt;" in out


def test_error_html_without_last_run(ticket_fetch):
    # sanity: the optional line is just absent when not provided
    out = ticket_fetch.generate_error_html("boom")
    assert "Last successful run" not in out
    assert "boom" in out


def test_empty_html_escapes_message(ticket_fetch):
    out = ticket_fetch.generate_empty_html("<img src=x onerror=alert(1)>")
    assert "<img" not in out
    assert "&lt;img" in out
