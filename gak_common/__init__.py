"""Code shared across the gak-parsers cron tools.

Each tool (reddit/, tippspiel/, tickets/) is an independent cron script, but
they share a few small helpers -- notably logging setup. They live here rather
than under any single tool so nothing depends on a sibling tool's directory.

Scripts bootstrap this package onto ``sys.path`` at import time (see the
``sys.path.insert`` snippet near the top of each script), because cron runs
them as ``cd <tool>; python <script>.py`` and the repo root is not otherwise
importable.
"""
