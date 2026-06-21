"""Pytest fixtures that load the cron scripts as importable modules.

The scripts under reddit/, tippspiel/, and tickets/ use hyphens in their
filenames (e.g. `reddit-create.py`), so they cannot be imported with a
regular `import` statement. We load them via importlib instead, making sure
each script's directory is on sys.path so sibling packages (e.g. tickets/lib)
resolve correctly.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Make tickets/lib importable for direct module tests (test_db.py).
TICKETS = ROOT / "tickets"
if str(TICKETS) not in sys.path:
    sys.path.insert(0, str(TICKETS))


def _load_script(script_rel):
    """Load a script file as a module and return it."""
    full = ROOT / script_rel
    parent = str(full.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(full.stem.replace("-", "_"), full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def reddit_create():
    return _load_script("reddit/reddit-create.py")


@pytest.fixture(scope="session")
def tippspiel_table():
    return _load_script("tippspiel/tippspiel-table.py")


@pytest.fixture(scope="session")
def ticket_fetch():
    return _load_script("tickets/ticket-fetch.py")
