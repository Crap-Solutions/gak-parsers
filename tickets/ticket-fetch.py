#!/usr/bin/env python3
"""Fetch GAK ticket data from API and optionally generate HTML page.
Runs via cron every 5 minutes.
"""

import argparse
import html
import io
import logging
import os
import sys
import traceback
import base64
import datetime
import dateutil.parser
from pathlib import Path

import jinja2

from lib import db, api, graph, corrections

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def generate_error_html(error_message, last_successful_run=None):
    """Generate HTML error page.

    Both inputs are HTML-escaped: ``error_message`` is frequently upstream
    or exception text (e.g. a FetchError echoing a server response) and the
    page is served publicly, so unescaped interpolation would be an
    injection vector.
    """
    error_message = html.escape(str(error_message))
    last_run_html = (
        f"    <p><em>Last successful run: {html.escape(str(last_successful_run))}</em></p>"
        if last_successful_run else "")
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
    """Generate HTML page for empty state.

    ``message`` is HTML-escaped for the same reason as the error page: it is
    rendered on a public page and could originate from upstream text.
    """
    message = html.escape(str(message))
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


def generate_mini_graph(event_id, event_time, conn):
    """Generate mini graph for a single event. Returns base64-encoded PNG or None.

    Reads through the caller-supplied (read-only) connection instead of opening
    one per event, so rendering N past-event cards doesn't open N connections.
    """
    try:
        entries = db.get_entries_for_event(conn, event_id)

        if not entries:
            return None

        hours = []
        sold = []
        for entry in entries:
            try:
                tickets_time = dateutil.parser.parse(entry[3])
                # Make both naive for comparison
                if tickets_time.tzinfo is not None:
                    tickets_time = tickets_time.replace(tzinfo=None)
                event_time_naive = event_time.replace(tzinfo=None) if event_time.tzinfo else event_time

                h_diff = (event_time_naive - tickets_time).total_seconds() / 3600

                # Apply corrections
                tickets_sold = corrections.apply_ticket_corrections(event_id, entry[1], h_diff)

                hours.append(h_diff)
                sold.append(tickets_sold)
            except (dateutil.parser.ParserError, ValueError, KeyError):
                continue

        if not hours or not sold:
            return None

        # Filter to last 300 hours
        filtered_hours = [h for h in hours if h <= 300]
        filtered_sold = [s for h, s in zip(hours, sold) if h <= 300]

        if not filtered_hours:
            return None

        # Create mini graph
        fig, ax = plt.subplots(figsize=(3, 1.5))
        ax.plot(filtered_hours, filtered_sold, color='#d9534f', linewidth=1.5)
        ax.set_xlim([0, 300])
        ax.invert_xaxis()
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_linewidth(0.5)
        ax.grid(False)

        tmpfile = io.BytesIO()
        plt.savefig(tmpfile, format='png', dpi=80, bbox_inches='tight', pad_inches=0.05)
        img = base64.b64encode(tmpfile.getvalue()).decode('utf-8')
        plt.close()

        return img
    except Exception as e:
        logger.warning(f"Failed to generate mini graph for {event_id}: {e}")
        return None


def generate_page(db_path, out_path, template_dir='templates'):
    """Generate HTML page from database."""
    # Connect to database. Read-only: generate_page only reads, and the data
    # was already written by the fetch loop in main(); a shared connection is
    # reused for every event + mini-graph instead of one per section/card.
    try:
        conn = db.open_connection(str(db_path), read_only=True)
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        html_content = generate_error_html(f"Database connection failed: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        return False

    # Get events from database (only future events)
    events_data = db.get_events(conn)
    if not events_data:
        logger.info("No events found in database")
        html_content = generate_empty_html()
        out_path.write_text(html_content, encoding='utf-8')
        conn.close()
        return False

    # Parse events for template with latest sold/avail data (future events only)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    events = []
    for entry in events_data:
        event_id = entry[0]
        try:
            event_time = dateutil.parser.parse(entry[2])
            # Strip timezone info for comparison
            event_time = event_time.replace(tzinfo=None)
        except (dateutil.parser.ParserError, ValueError) as e:
            logger.warning(f"Error parsing date for event {entry[0]}: {e}")
            continue

        # Only show future events
        if event_time < now:
            continue

        # Get all entries for this event
        entries = db.get_entries_for_event(conn, event_id)
        if entries:
            # Latest entry is the last one
            latest = entries[-1]
            event_data = {
                "title": entry[1],
                "id": event_id,
                "sold": latest[1],
                "avail": latest[2],
            }

            # Calculate capacity percentage (assuming ~15000 capacity)
            total_sold = latest[1] + 2333 + 285 + 296  # w/ Sponsors, VIP, etc.
            capacity = int((total_sold / 15000) * 100)
            event_data["capacity_percent"] = capacity

            # Calculate sales velocity
            if len(entries) >= 2:
                # Get timestamps for velocity calculation
                timestamps = []
                for ent in entries:
                    try:
                        ts = dateutil.parser.parse(ent[3])
                        if ts.tzinfo:
                            ts = ts.replace(tzinfo=None)
                        timestamps.append((ts, ent[1]))
                    except:
                        continue

                if len(timestamps) >= 2:
                    timestamps.sort()

                    # Calculate velocity as difference between oldest and newest in each time window
                    def get_sold_in_window(window_minutes):
                        window_ago = now - datetime.timedelta(minutes=window_minutes)
                        window_entries = [s for t, s in timestamps if t >= window_ago]
                        if len(window_entries) >= 2:
                            return max(window_entries) - min(window_entries)
                        return 0

                    ten_min_sold = get_sold_in_window(10)
                    one_hour_sold = get_sold_in_window(60)
                    one_day_sold = get_sold_in_window(1440)

                    velocity_parts = []
                    if one_day_sold > 0:
                        velocity_parts.append(f"{one_day_sold} tickets in last day")
                    if one_hour_sold > 0:
                        velocity_parts.append(f"{one_hour_sold} in last hour")
                    if ten_min_sold > 0:
                        velocity_parts.append(f"{ten_min_sold} in last 10min")

                    if velocity_parts:
                        event_data["velocity"] = " | ".join(velocity_parts)

            events.append(event_data)

    if not events:
        logger.info("No future events to display")
        html_content = generate_empty_html()
        out_path.write_text(html_content, encoding='utf-8')
        conn.close()
        return False

    # Fetch past events (from current season - July onwards). Reuse the same
    # read-only connection and the event list already fetched above; nothing
    # was written so it is still current.
    # Get current year, if we're before July, use last year's July
    current_year = now.year
    if now.month < 7:
        season_start = datetime.datetime(current_year - 1, 7, 1)
    else:
        season_start = datetime.datetime(current_year, 7, 1)

    past_events = []
    for entry in events_data:
        event_id = entry[0]
        try:
            event_time = dateutil.parser.parse(entry[2])
            event_time = event_time.replace(tzinfo=None)
        except (dateutil.parser.ParserError, ValueError) as e:
            continue

        # Only past events from current season
        if event_time >= now or event_time < season_start:
            continue

        # Get latest entry for this event
        entries = db.get_entries_for_event(conn, event_id)
        if entries:
            latest = entries[-1]
            # Apply the same per-event corrections the mini graph uses, so the
            # card's "Sold" figure (and the ranking / season average derived
            # from it) matches the corrected curve plotted beside it, instead
            # of the raw upstream number.
            corrected_sold = latest[1]
            try:
                latest_ts = dateutil.parser.parse(latest[3])
                if latest_ts.tzinfo is not None:
                    latest_ts = latest_ts.replace(tzinfo=None)
                latest_h_diff = (event_time - latest_ts).total_seconds() / 3600
                corrected_sold = corrections.apply_ticket_corrections(
                    event_id, latest[1], latest_h_diff)
            except (dateutil.parser.ParserError, ValueError) as e:
                logger.warning(f"Could not parse timestamp for past event {event_id}: {e}")
            # Extract away team name (remove "GAK 1902 : " prefix)
            title = entry[1]
            if " : " in title:
                title = title.split(" : ", 1)[1]
            # Generate mini graph for this event
            mini_graph = generate_mini_graph(event_id, event_time, conn)
            past_events.append({
                "title": title,
                "date": event_time.strftime('%Y-%m-%d'),
                "sold": corrected_sold,
                "graph": mini_graph,
                "event_id": event_id,
            })

    # Sort by sold descending for ranking
    past_events.sort(key=lambda x: x['sold'], reverse=True)

    # Add rankings and mark top 3
    for i, event in enumerate(past_events):
        event['rank'] = i + 1
        if i < 3:
            event['top_performer'] = True

    # Re-sort by date for display
    past_events.sort(key=lambda x: x['date'], reverse=True)

    # Create season summary
    if past_events:
        total_sold = sum(e['sold'] for e in past_events)
        avg_sold = total_sold // len(past_events)
        season_summary = f"{len(past_events)} matches this season, average {avg_sold} tickets sold"
    else:
        season_summary = None

    conn.close()

    # Generate graph
    try:
        img = graph.generate_graph(str(db_path))
        if img is None:
            logger.error("Failed to generate graph, using fallback page")
            html_content = generate_error_html("Failed to generate sales graph")
            out_path.write_text(html_content, encoding='utf-8')
            return False
    except Exception as e:
        logger.error(f"Critical error in graph generation: {e}")
        logger.debug(traceback.format_exc())
        html_content = generate_error_html(f"Critical error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        return False

    # Render HTML
    try:
        templ_path = Path(template_dir)
        jenv = jinja2.Environment(loader=jinja2.FileSystemLoader(str(templ_path)))
        ticket_tmpl = jenv.get_template("ticket-html.tmpl")

        # Last updated timestamp
        last_updated = now.strftime('%Y-%m-%d %H:%M:%S')

        html_content = ticket_tmpl.render(events=events, img=img, past_events=past_events,
                                         season_summary=season_summary, last_updated=last_updated)

        # Write to output file
        out_path.write_text(html_content, encoding='utf-8')
        logger.info(f"Successfully generated ticket report: {out_path}")
        return True

    except jinja2.TemplateError as e:
        logger.error(f"Template rendering error: {e}")
        html_content = generate_error_html(f"Template error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.debug(traceback.format_exc())
        html_content = generate_error_html(f"Unexpected error: {e}")
        out_path.write_text(html_content, encoding='utf-8')
        return False


def _resolve_log_file(candidates):
    """Return the first writable log file path from candidates, or None.

    Tries each candidate in order, creating its parent directory if needed.
    A warning is printed to stderr for any candidate that cannot be opened,
    so the failure is visible in cron mail even when stdout is discarded.
    """
    for candidate in candidates:
        try:
            Path(candidate).parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(candidate)
            handler.close()
            return candidate
        except (PermissionError, OSError) as e:
            print(f"WARNING: cannot open log file {candidate}: {e}", file=sys.stderr)
            continue
    return None


def _resolve_log_level(cli_level):
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


# The upstream ticket server has regular, short downtimes. Cron polls every
# 5 minutes, so alerting on the first failure of every outage would flood the
# inbox. Instead we suppress the cron email (stdout) for this long after a
# fetch failure starts, and only mail once an outage has persisted past it.
DEFAULT_ALERT_GRACE = 24 * 60 * 60  # 24 hours


def _resolve_alert_grace(cli_value):
    """Resolve the alert-grace window in seconds.

    Precedence: --alert-grace flag > $GAK_ALERT_GRACE env > 24h default.
    0 disables the window (alert immediately on every failure, the legacy
    behaviour). Like ``--log-level`` this only governs the cron-facing stdout
    output; failures are always recorded in the file log.
    """
    raw = cli_value if cli_value is not None else os.environ.get("GAK_ALERT_GRACE")
    if not raw:
        return DEFAULT_ALERT_GRACE
    try:
        value = int(raw)
    except ValueError:
        print(f"WARNING: invalid alert grace {raw!r}, "
              f"defaulting to {DEFAULT_ALERT_GRACE}s", file=sys.stderr)
        return DEFAULT_ALERT_GRACE
    if value < 0:
        print(f"WARNING: negative alert grace {value}, treating as 0 (disabled)",
              file=sys.stderr)
        return 0
    return value


def _failure_state_path(db_path, override=None):
    """Return the path of the fetch-failure state file.

    Defaults to ``<db>.failstate`` so it lives alongside the database (always
    writable by the cron job) and survives across runs.
    """
    if override:
        return Path(override)
    return Path(str(db_path) + ".failstate")


def _read_failure_since(state_path):
    """Return the start timestamp of the current outage, or None."""
    try:
        text = state_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _within_grace(state_path, grace_seconds, now=None):
    """Decide whether a fetch failure is still inside the alert-grace window.

    Reads the outage start timestamp from ``state_path``; if none is recorded
    yet (first failure of a new outage) it is stamped now. Returns True when
    the outage is younger than ``grace_seconds`` (suppress the cron email and
    keep serving the last good page), False once the window has elapsed (alert
    normally). A non-positive ``grace_seconds`` disables grace entirely.

    On any problem reading or writing the state file we fail safe and return
    False (alert) rather than silently swallowing an outage.
    """
    if grace_seconds <= 0:
        return False
    now = now or datetime.datetime.now(datetime.timezone.utc)
    since = _read_failure_since(state_path)
    if since is None:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(now.isoformat(), encoding="utf-8")
        except OSError as e:
            logger.warning(
                f"Cannot write failure state {state_path}: {e}; alerting")
            return False
        return True  # outage just started -> within grace
    if since.tzinfo is None:
        since = since.replace(tzinfo=datetime.timezone.utc)
    return (now - since).total_seconds() < grace_seconds


def _clear_failure_state(state_path):
    """Clear the outage state after a successful fetch (server is back up)."""
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Could not clear failure state {state_path}: {e}")


def _log_to_file_only(level, msg):
    """Emit a log record to file handlers only, bypassing stdout.

    During the alert-grace window we still want the failure on disk for
    debugging, but it must not reach cron's stdout (which triggers an email
    every 5 minutes).
    """
    record = logger.makeRecord(logger.name, level, __file__, 0, msg, None, None)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.handle(record)


def main():
    parser = argparse.ArgumentParser(description='Fetch GAK ticket data from API and optionally generate HTML')
    parser.add_argument('--db', default='data/ticket.db',
                        help='Path to SQLite database (default: data/ticket.db)')
    parser.add_argument('--output', default='output/index.html',
                        help='Output HTML file path (default: output/index.html)')
    parser.add_argument('--templates', default='templates',
                        help='Template directory path (default: templates)')
    parser.add_argument('--timeout', type=int, default=api.REQUEST_TIMEOUT,
                        help='Request timeout in seconds (default: 30)')
    parser.add_argument('--log', default=None,
                        help='Log file path (default: /var/log/gak-ticket.log, or stdout only if not writable)')
    parser.add_argument('--log-level', default=None,
                        help='Stdout log level (default: INFO). Also set via $GAK_LOG_LEVEL. '
                             'Use WARNING in cron to stay silent on success.')
    parser.add_argument('--alert-grace', type=int, default=None,
                        help='Seconds to suppress the cron alert email after a fetch '
                             'failure starts (the upstream server has regular short '
                             'downtimes and cron polls every 5 min). Default 24h; '
                             '0 disables it and alerts immediately. Also set via '
                             '$GAK_ALERT_GRACE.')
    parser.add_argument('--failstate', default=None,
                        help='Path to the fetch-failure state file used by '
                             '--alert-grace (default: <db>.failstate).')
    parser.add_argument('--generate', action='store_true',
                        help='Generate HTML page after fetching data')

    args = parser.parse_args()

    # Apply the resolved stdout log level to the stdout handler (created at
    # module import via basicConfig on the root logger). The file handler
    # added below stays at INFO, so on-disk detail is always preserved.
    stdout_level = _resolve_log_level(args.log_level)
    logging.getLogger().setLevel(logging.INFO)
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(stdout_level)

    # Configure file logging. The primary default (/var/log/gak-ticket.log)
    # is used when writable (e.g. cron running as root). Otherwise fall back
    # to a file next to the output so logs survive a non-root cron job
    # instead of vanishing silently with stdout.
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    primary_log = args.log or '/var/log/gak-ticket.log'
    fallback_log = str(Path(__file__).resolve().parent / 'output' / 'ticket-fetch.log')
    chosen = _resolve_log_file([primary_log, fallback_log])
    if chosen is not None:
        file_handler = logging.FileHandler(chosen)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        if chosen != primary_log:
            print(f"WARNING: {primary_log} not writable; logging to {chosen}",
                  file=sys.stderr)
    else:
        print("WARNING: no writable log file available; logging to stdout only",
              file=sys.stderr)

    # Ensure data directory exists
    db_path = Path(args.db)
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure output directory exists
    out_path = Path(args.output)
    if not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Alert-grace window: the upstream server has regular short downtimes and
    # cron polls every 5 minutes, so we only email after a fetch failure has
    # persisted past this window. See _within_grace().
    grace_seconds = _resolve_alert_grace(args.alert_grace)
    state_path = _failure_state_path(db_path, args.failstate)

    # Initialize database
    try:
        conn = db.init_db(str(db_path))
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        if args.generate:
            html_content = generate_error_html(f"Database initialization failed: {e}")
            out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)

    # Fetch events
    base_url = "https://ticket.grazerak.at/backend/events/"
    events_ep = "futurePublishedEvents"

    try:
        events_data = api.fetch_events(base_url, events_ep, args.timeout)
    except api.FetchError as e:
        msg = f"Failed to fetch events: {e}"
        if _within_grace(state_path, grace_seconds):
            # Transient upstream blip: record on disk only (not stdout, which
            # would mail cron) and keep serving the last good page instead of
            # overwriting it every 5 minutes. Alert once it persists ~24h.
            _log_to_file_only(logging.ERROR, msg)
            logger.info("Upstream fetch failing; within alert-grace window, "
                        "suppressing cron email")
            if args.generate and not out_path.exists():
                out_path.write_text(generate_error_html(msg), encoding='utf-8')
            sys.exit(0)
        # Grace window elapsed: sustained outage -> alert normally.
        logger.error(msg)
        if args.generate:
            out_path.write_text(generate_error_html(msg), encoding='utf-8')
        sys.exit(1)

    # Server responded (even with an empty list): any outage is over.
    _clear_failure_state(state_path)

    if not events_data:
        logger.info("No events found from API")
        if args.generate:
            html_content = generate_empty_html()
            out_path.write_text(html_content, encoding='utf-8')
        sys.exit(0)

    # Process events
    events_updated = 0
    for event in events_data:
        event_id = event.get("id")
        if not event_id:
            logger.warning(f"Event missing ID, skipping")
            continue

        try:
            content = api.fetch_event_details(base_url, event_id, args.timeout)
        except api.FetchError as e:
            logger.error(f"{e}; skipping")
            continue

        parsed = api.parse_event_data(event, content)
        if parsed:
            if db.update_event(conn, event, parsed):
                events_updated += 1
        else:
            logger.error(f"Failed to parse event {event_id}, skipping")

    conn.commit()
    conn.close()

    logger.info(f"Updated {events_updated} event(s)")

    # Generate HTML page if requested
    if args.generate:
        if not generate_page(db_path, out_path, args.templates):
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
