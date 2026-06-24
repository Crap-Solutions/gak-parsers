"""Shared logging setup for the gak-parsers cron tools.

Two helpers used by every cron script:

- ``resolve_log_level`` resolves the stdout log level from the ``--log-level``
  flag / ``$GAK_LOG_LEVEL`` env / INFO default.
- ``setup_logging`` wires a stdout handler (filtered by that level) plus an
  optional always-INFO file handler.

The key contract for cron users: setting the level to WARNING silences INFO on
stdout (so a successful cron run produces no output -> no mail) while the file
log still captures INFO for debugging. ticket-fetch.py has its own, richer
file-log handling (an alert-grace fallback path) and so only imports
``resolve_log_level``.
"""
import logging
import os
import sys


def resolve_log_level(cli_level):
    """Resolve the effective stdout log level.

    Precedence: --log-level flag > $GAK_LOG_LEVEL env > INFO default.
    Accepts level names (DEBUG/INFO/WARNING/ERROR/CRITICAL) or numbers.
    This only affects the stdout handler, so cron can run at WARNING
    (silent on success, mails on failure) while the file log keeps INFO.
    """
    raw = cli_level or os.environ.get("GAK_LOG_LEVEL")
    if not raw:
        return logging.INFO
    try:
        return int(raw)
    except ValueError:
        pass
    level = logging.getLevelName(str(raw).upper())
    if isinstance(level, int):
        return level
    print(f"WARNING: invalid log level {raw!r}, defaulting to INFO", file=sys.stderr)
    return logging.INFO


def setup_logging(log_file=None, stdout_level=logging.INFO):
    """Configure logging to stdout plus an optional file.

    In a cron context stdout is usually discarded, so a writable log file
    is important for post-mortem debugging. If the requested file cannot
    be opened we warn on stderr (cron captures stderr in mail) and carry
    on with stdout-only logging rather than failing silently.

    `stdout_level` filters the stdout/cron handler; the file handler is
    always kept at INFO so on-disk detail is preserved.
    """
    root = logging.getLogger()
    # Root must be at most INFO so the file handler still sees INFO records.
    root.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    if not root.handlers:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        sh.setLevel(stdout_level)
        root.addHandler(sh)

    if log_file:
        try:
            parent = os.path.dirname(log_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except (PermissionError, OSError) as e:
            print(f"WARNING: could not open log file {log_file}: {e}; "
                  f"logging to stdout only", file=sys.stderr)
