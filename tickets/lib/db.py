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
        return conn
    except sqlite3.Error as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def update_event(conn, event, entry):
    """Update database with event and entry data."""
    try:
        conn.execute('''
            INSERT OR IGNORE INTO EVENTS
            (ID, TITLE, DATETIME, SELLFROM, SELLTO) VALUES
            (:id, :title, :dateTimeFrom, :publiclyAvailableFrom,
            :publiclyAvailableTo)
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
    """Get all entries for a specific event."""
    try:
        cur = conn.execute("SELECT * FROM ENTRIES WHERE MATCH=?", (event_id,))
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
