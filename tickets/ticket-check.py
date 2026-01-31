#!/usr/bin/env python3
import argparse
import io
import logging
import sys
from pathlib import Path
import sqlite3
import datetime
import base64
import traceback
from contextlib import contextmanager

import requests
import jinja2
import dateutil.parser
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for cron
import matplotlib.pyplot as plt


# Request timeout (in seconds)
REQUEST_TIMEOUT = 30

# Setup basic logger (stdout only, will be reconfigured in main())
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


@contextmanager
def database_connection(db_path):
    """Context manager for database connections with error handling."""
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def init_db(db_file):
    """Initialize database schema."""
    try:
        conn = sqlite3.connect(str(db_file))
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


def update_db(conn, event, entry):
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


def fetch_events(base_url, events_ep, timeout=REQUEST_TIMEOUT):
    """Fetch events from API with error handling."""
    try:
        response = requests.get(base_url + events_ep, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            logger.error(f"Expected list from events API, got {type(data)}")
            return []

        return data
    except requests.exceptions.Timeout:
        logger.error("Timeout fetching events from API")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch events: {e}")
        return []
    except ValueError as e:
        logger.error(f"Invalid JSON response: {e}")
        return []


def fetch_event_details(base_url, event_id, timeout=REQUEST_TIMEOUT):
    """Fetch event details from API with error handling."""
    try:
        event_url = base_url + event_id + "/"
        chk_url = event_url + "public-stadium-representation-config"
        response = requests.get(chk_url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching details for event {event_id}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch details for event {event_id}: {e}")
        return None
    except ValueError as e:
        logger.error(f"Invalid JSON response for event {event_id}: {e}")
        return None


def parse_event_data(event, content):
    """Parse event data from API response with validation."""
    try:
        if not isinstance(content, dict):
            logger.error(f"Event {event.get('id')}: expected dict, got {type(content)}")
            return None

        if 'sectorRepresentationConfigurations' not in content:
            logger.error(f"Event {event.get('id')}: missing sectorRepresentationConfigurations")
            return None

        if not isinstance(content['sectorRepresentationConfigurations'], list):
            logger.error(f"Event {event.get('id')}: sectorRepresentationConfigurations is not a list")
            return None

        sold_cnt = 0
        avail_cnt = 0
        for entry in content['sectorRepresentationConfigurations']:
            if not isinstance(entry, dict):
                continue

            seat_configs = entry.get('seatConfigurations', [])
            if not isinstance(seat_configs, list):
                logger.warning(f"Event {event.get('id')}: invalid seatConfigurations, skipping")
                continue

            for e in seat_configs:
                if e.get('seatStatus') == 'SOLD':
                    sold_cnt += 1
                elif e.get('seatStatus') == 'AVAILABLE':
                    avail_cnt += 1

        return {
            "title": event.get("title", "Unknown"),
            "id": event.get("id", ""),
            "sold": sold_cnt,
            "avail": avail_cnt
        }
    except Exception as e:
        logger.error(f"Error parsing event data: {e}")
        return None


def draw_graph(db_path):
    """Generate sales graph with error handling."""
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)

        # Query events
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
        events = cur.fetchall()

        if not events:
            logger.warning("No events found to graph")
            return None

        parsed_events = []
        for entry in events:
            event = {}
            event['id'] = entry[0]
            event['title'] = entry[1]
            try:
                event['time'] = dateutil.parser.parse(entry[2])
                event['sellfrom'] = dateutil.parser.parse(entry[3])
                event['sellto'] = dateutil.parser.parse(entry[4])
            except (dateutil.parser.ParserError, ValueError) as e:
                logger.error(f"Error parsing date for event {entry[0]}: {e}")
                continue
            parsed_events.append(event)

        if not parsed_events:
            logger.warning("No valid events to graph")
            return None

        # Create graph
        matplotlib.pyplot.style.use('tableau-colorblind10')
        matplotlib.pyplot.figure(figsize=(10, 6))
        matplotlib.pyplot.title("Ticket Sales Over Time")
        matplotlib.pyplot.xlabel("Hours Until Match")
        matplotlib.pyplot.ylabel("Tickets Sold (Online Available)")
        matplotlib.pyplot.grid(True, linestyle='--', alpha=0.7)

        for event in parsed_events:
            try:
                cur = conn.execute("SELECT * FROM ENTRIES WHERE MATCH=?", (event['id'],))
                entries = cur.fetchall()

                hours = []
                sold = []
                for entry in entries:
                    tickets = {}
                    tickets['sold'] = entry[1]
                    try:
                        tickets['time'] = dateutil.parser.parse(entry[3])
                        tickets['time'] = tickets['time'].replace(tzinfo=datetime.timezone.utc)
                        h_diff = (event['time']-tickets['time']).total_seconds() / 3600
                        tickets['diff'] = h_diff

                        # Apply corrections (TODO: remove these hardcoded fixes)
                        if event['id'] == "456e9a8a-ce64-4580-b9e0-3405a810c696":
                            if tickets['sold'] >= 2302:
                                tickets['sold'] = tickets['sold'] - 1939
                        if event['id'] == "aeee2d94-edae-4a6d-a65c-f2ae274361ef":
                            if tickets['sold'] >= 5100 and tickets['diff'] > 74.20:
                                tickets['sold'] = tickets['sold'] - 285
                        if event['id'] == "2e9e16ba-e8c3-409e-8c41-d7e6ddfaab40":
                            if tickets['diff'] < 96.35:
                                tickets['sold'] = tickets['sold'] + 296 + 285
                            if tickets['diff'] < 49.8:
                                tickets['sold'] = tickets['sold'] + 2333
                        hours.append(h_diff)
                        sold.append(tickets['sold'])

                    except (dateutil.parser.ParserError, ValueError, KeyError) as e:
                        logger.warning(f"Error parsing entry for event {event['id']}: {e}")
                        continue

                if hours and sold:
                    matplotlib.pyplot.plot(hours, sold, label=event['title'])

            except sqlite3.Error as e:
                logger.error(f"Database error while fetching entries for {event['id']}: {e}")
                continue

        if not matplotlib.pyplot.gca().has_data():
            logger.warning("No data to plot")
            return None

        matplotlib.pyplot.gca().set_xlim([0, 600])
        matplotlib.pyplot.gca().invert_xaxis()
        matplotlib.pyplot.gca().xaxis.get_major_locator().set_params(integer=True)
        matplotlib.pyplot.legend(loc='upper left', bbox_to_anchor=(1, 1), fontsize='small')
        matplotlib.pyplot.tight_layout()

        tmpfile = io.BytesIO()
        matplotlib.pyplot.savefig(tmpfile, format='png')
        img = base64.b64encode(tmpfile.getvalue()).decode('utf-8')

        matplotlib.pyplot.close()
        conn.close()
        return img

    except sqlite3.Error as e:
        logger.error(f"Database error in draw_graph: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating graph: {e}")
        logger.debug(traceback.format_exc())
        return None


def generate_error_html(error_message, last_successful_run=None):
    """Generate HTML error page."""
    last_run_html = f"    <p><em>Last successful run: {last_successful_run}</em></p>" if last_successful_run else ""
    return f'''<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="300">
<title>GAK ticket watch - ERROR</title>
<style>
    body {{
        font-family: Arial, sans-serif;
        background-color: #f4f4f4;
        color: #333;
        margin: 0;
        padding: 20px;
    }}
    .error {{
        background-color: #fee;
        border: 1px solid #cc0000;
        border-radius: 5px;
        padding: 15px;
        margin: 20px 0;
    }}
    h1 {{ color: #cc0000; }}
</style>
</head>
<body>
    <div class="error">
        <h1>⚠️ Error</h1>
        <p><strong>{error_message}</strong></p>
        <p>Next automatic run in 5 minutes.</p>
    </div>
{last_run_html}
</body>
</html>'''


def generate_empty_html(message="No upcoming events found"):
    """Generate HTML page for empty state."""
    return f'''<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="300">
<title>GAK ticket watch</title>
<style>
    body {{
        font-family: Arial, sans-serif;
        background-color: #f4f4f4;
        color: #333;
        margin: 0;
        padding: 20px;
    }}
    .info {{
        background-color: #e8f4ff;
        border: 1px solid #b3d9ff;
        border-radius: 5px;
        padding: 15px;
        margin: 20px 0;
    }}
</style>
</head>
<body>
    <div class="info">
        <h1>GAK Ticket Watch</h1>
        <p>{message}</p>
    </div>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser(description='Track GAK ticket sales and generate HTML report')
    parser.add_argument('--db', default='data/ticket.db',
                        help='Path to SQLite database (default: data/ticket.db)')
    parser.add_argument('--output', default='output/index.html',
                        help='Output HTML file path (default: output/index.html)')
    parser.add_argument('--timeout', type=int, default=REQUEST_TIMEOUT,
                        help='Request timeout in seconds (default: 30)')
    parser.add_argument('--log', default=None,
                        help='Log file path (default: /var/log/gak-ticket.log, or stdout only if not writable)')

    args = parser.parse_args()

    # Reconfigure logging to add file handler if requested
    log_file = args.log or '/var/log/gak-ticket.log'
    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(file_handler)
    except (PermissionError, OSError):
        # Fallback to stdout only if log file is not writable
        pass

    # Ensure data directory exists
    db_path = Path(args.db)
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure output directory exists
    out_path = Path(args.output)
    if not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize database
    try:
        conn = init_db(str(db_path))
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        html_content = generate_error_html(f"Database initialization failed: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)

    # Fetch events
    base_url = "https://ticket.grazerak.at/backend/events/"
    events_ep = "futurePublishedEvents"

    try:
        events_data = fetch_events(base_url, events_ep, args.timeout)
    except Exception as e:
        logger.error(f"Failed to fetch events: {e}")
        html_content = generate_error_html(f"Failed to fetch events: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)

    if not events_data:
        logger.info("No events found from API")
        html_content = generate_empty_html()
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(0)

    # Process events
    events = []
    event_ids_to_delete = set()

    for event in events_data:
        event_id = event.get("id")
        if not event_id:
            logger.warning(f"Event missing ID, skipping")
            continue

        content = fetch_event_details(base_url, event_id, args.timeout)
        if content is None:
            logger.error(f"Could not fetch details for event {event_id}, skipping")
            event_ids_to_delete.add(event_id)
            continue

        parsed = parse_event_data(event, content)
        if parsed:
            if update_db(conn, event, parsed):
                events.append(parsed)
        else:
            logger.error(f"Failed to parse event {event_id}, skipping")

    conn.commit()
    conn.close()

    if not events:
        logger.info("No valid events to display")
        html_content = generate_empty_html()
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(0)

    # Generate graph
    try:
        img = draw_graph(str(db_path))
        if img is None:
            logger.error("Failed to generate graph, using fallback page")
            html_content = generate_error_html("Failed to generate sales graph")
            out_path.write_text(html_content, encoding='utf-8')
            sys.exit(1)
    except Exception as e:
        logger.error(f"Critical error in graph generation: {e}")
        html_content = generate_error_html(f"Critical error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)

    # Render HTML
    try:
        cur_path = Path(__file__).parent
        templ_path = cur_path / "templates"
        jenv = jinja2.Environment(loader=jinja2.FileSystemLoader(str(templ_path)))
        ticket_tmpl = jenv.get_template("ticket-html.tmpl")
        html_content = ticket_tmpl.render(events=events, img=img)

        # Write to output file
        out_path.write_text(html_content, encoding='utf-8')
        logger.info(f"Successfully generated ticket report: {out_path}")

    except jinja2.TemplateError as e:
        logger.error(f"Template rendering error: {e}")
        html_content = generate_error_html(f"Template error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.debug(traceback.format_exc())
        html_content = generate_error_html(f"Unexpected error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)


if __name__ == "__main__":
    main()
