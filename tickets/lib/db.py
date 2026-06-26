"""Database operations for GAK ticket tracking."""

import sqlite3
import logging

logger = logging.getLogger(__name__)

# Seconds to wait for a database lock before failing. SQLite has no
# network-style connect timeout; this busy timeout governs how long a
# connection waits for another connection (e.g. a previous cron run still
# writing) to release its lock before raising OperationalError("database
# is locked"). Applied via both the connect() timeout argument and
# PRAGMA busy_timeout (milliseconds) for robustness.
DB_TIMEOUT = 30

# ENTRIES rows older than this are pruned each run. One row is inserted per
# event per 5-minute cron poll, so the table would otherwise grow without
# bound; display only ever looks at the last ~600 hours (main graph) / 300
# hours (mini graphs), so ~25 days is a hard floor and 60 gives a wide
# margin for ad-hoc analysis without bloating every query.
RETENTION_DAYS = 60


def open_connection(db_path, read_only=False):
    """Open a SQLite connection with a consistent busy timeout.

    All database access should go through here so that lock contention
    fails after DB_TIMEOUT seconds instead of hanging indefinitely or
    failing spuriously. Returns a connection whose busy_timeout is set;
    callers are responsible for close()-ing it.
    """
    try:
        if read_only:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True,
                                   timeout=DB_TIMEOUT)
        else:
            conn = sqlite3.connect(str(db_path), timeout=DB_TIMEOUT)
        conn.execute(f'PRAGMA busy_timeout = {int(DB_TIMEOUT * 1000)}')
        return conn
    except sqlite3.Error as e:
        logger.error(f"Failed to open database {db_path}: {e}")
        raise


def init_db(db_file):
    """Initialize database schema."""
    try:
        conn = open_connection(db_file)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS EVENTS
            (
                ID          TEXT        PRIMARY KEY NOT NULL,
                TITLE       TEXT                    NOT NULL,
                DATETIME    DATETIME                NOT NULL,
                SELLFROM    DATETIME                NOT NULL,
                SELLTO      DATETIME                NOT NULL
            );''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS ENTRIES
            (
                MATCH       TEXT        NOT NULL,
                SOLD        INTEGER     NOT NULL,
                AVAILABLE   INTEGER     NOT NULL,
                TIMESTAMP   DATETIME    NOT NULL    DEFAULT CURRENT_TIMESTAMP
            );''')

        # get_entries_for_event filters WHERE MATCH=? and renderers take the
        # last row as "latest"; without this index every per-event lookup is a
        # full scan, which slows as the table grows (see prune_old_entries).
        conn.execute('''
            CREATE INDEX IF NOT EXISTS IDX_ENTRIES_MATCH
            ON ENTRIES(MATCH, TIMESTAMP)
            ''')
        return conn
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def update_event(conn, event, entry):
    """Update database with event and entry data."""
    try:
        conn.execute('''
            INSERT INTO EVENTS
            (ID, TITLE, DATETIME, SELLFROM, SELLTO) VALUES
            (:id, :title, :dateTimeFrom, :publiclyAvailableFrom,
            :publiclyAvailableTo)
            ON CONFLICT(ID) DO UPDATE SET
              TITLE   = excluded.TITLE,
              DATETIME = excluded.DATETIME,
              SELLFROM = excluded.SELLFROM,
              SELLTO   = excluded.SELLTO
            ''', event)

        conn.execute('''
            INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE) VALUES
            (:id, :sold, :avail)''', entry)
        return True
    except sqlite3.Error as e:
        logger.error(f"Failed to update database for event {event.get('id', 'unknown')}: {e}")
        return False


def get_events(conn):
    """Get all events from database."""
    try:
        cur = conn.execute("SELECT * FROM events")
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Failed to get events: {e}")
        return []


def get_entries_for_event(conn, event_id):
    """Get all entries for a specific event, oldest first.

    Ordered by TIMESTAMP so callers taking [-1] get the genuinely latest
    sample regardless of physical row order; served by IDX_ENTRIES_MATCH.
    """
    try:
        cur = conn.execute(
            "SELECT * FROM ENTRIES WHERE MATCH=? ORDER BY TIMESTAMP",
            (event_id,))
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Failed to get entries for event {event_id}: {e}")
        return []


def get_events_for_graph(conn):
    """Get events needed for graph generation."""
    try:
        cur = conn.execute('''
            SELECT * FROM (SELECT * FROM events WHERE id IN
            (SELECT match FROM entries ORDER BY sold DESC LIMIT 1))
            UNION SELECT * FROM
            (SELECT * FROM events WHERE datetime < DATE('now')
             ORDER BY datetime DESC LIMIT 1)
            UNION SELECT * FROM
            (SELECT * FROM events WHERE datetime > DATE('now'))
            ORDER BY datetime
            ''')
        return cur.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Failed to get events for graph: {e}")
        return []


def prune_old_entries(conn, days=RETENTION_DAYS):
    """Delete ENTRIES rows older than ``days`` to bound table growth.

    Safe to run after a successful fetch+commit. Only the high-volume sales
    samples are trimmed; EVENTS metadata is preserved. TIMESTAMP and
    datetime('now') are both UTC, so the comparison is consistent. Returns
    the number of rows deleted, or 0 on error (a failed prune is logged but
    never fatal -- it just retries next run).
    """
    try:
        cur = conn.execute(
            "DELETE FROM ENTRIES WHERE TIMESTAMP < datetime('now', ?)",
            (f'-{int(days)} days',))
        conn.commit()
        return cur.rowcount
    except sqlite3.Error as e:
        logger.warning(f"Failed to prune old entries: {e}")
        return 0
