"""Hygiene checks that span all cron scripts.

These guard against regressions that are easy to miss in review: a shebang
pinned to a Python minor version (the host runs 3.12, not 3.11) or a leftover
Python-2 ``__future__`` import.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = [
    "reddit/reddit-create.py",
    "tippspiel/tippspiel-table.py",
    "tickets/ticket-fetch.py",
]


def test_shebangs_use_unversioned_python3():
    """Scripts must not pin a minor Python version in their shebang."""
    for rel in SCRIPTS:
        first_line = (ROOT / rel).read_text().splitlines()[0]
        assert first_line == "#!/usr/bin/env python3", \
            f"{rel}: bad shebang {first_line!r}"


def test_tippspiel_has_no_py2_print_future():
    """``from __future__ import print_function`` is a Python-2 artifact."""
    text = (ROOT / "tippspiel" / "tippspiel-table.py").read_text()
    assert "from __future__ import print_function" not in text
