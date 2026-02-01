#!/usr/bin/env python3
"""Fetch GAK ticket data from API and optionally generate HTML page.
Runs via cron every 5 minutes.
"""

import argparse
import io
import logging
import sys
import traceback
import base64
import datetime
import dateutil.parser
from pathlib import Path

import jinja2

from lib import db, api, graph

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


def apply_ticket_corrections(event_id, tickets_sold, h_diff):
    """Apply data corrections for known ticket reporting issues."""
    if event_id == "456e9a8a-ce64-4580-b9e0-3405a810c696":
        if tickets_sold >= 2302:
            tickets_sold = tickets_sold - 1939
    if event_id == "aeee2d94-edae-4a6d-a65c-f2ae274361ef":
        if tickets_sold >= 5100 and h_diff > 74.20:
            tickets_sold = tickets_sold - 285
    if event_id == "2e9e16ba-e8c3-409e-8c41-d7e6ddfaab40":
        if h_diff < 96.35:
            tickets_sold = tickets_sold + 296 + 285
        if h_diff < 49.8:
            tickets_sold = tickets_sold + 2333
    return tickets_sold


def generate_mini_graph(event_id, event_time, db_path):
    """Generate mini graph for a single event. Returns base64-encoded PNG or None."""
    try:
        conn = db.init_db(str(db_path))
        entries = db.get_entries_for_event(conn, event_id)
        conn.close()

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
                tickets_sold = apply_ticket_corrections(event_id, entry[1], h_diff)

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
    # Connect to database
    try:
        conn = db.init_db(str(db_path))
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

    conn.close()

    if not events:
        logger.info("No future events to display")
        html_content = generate_empty_html()
        out_path.write_text(html_content, encoding='utf-8')
        return False

    # Fetch past events (from current season - July onwards)
    conn = db.init_db(str(db_path))
    events_data = db.get_events(conn)

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
            # Extract away team name (remove "GAK 1902 : " prefix)
            title = entry[1]
            if " : " in title:
                title = title.split(" : ", 1)[1]
            # Generate mini graph for this event
            mini_graph = generate_mini_graph(event_id, event_time, db_path)
            past_events.append({
                "title": title,
                "date": event_time.strftime('%Y-%m-%d'),
                "sold": latest[1],
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
    parser.add_argument('--generate', action='store_true',
                        help='Generate HTML page after fetching data')

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
    except Exception as e:
        logger.error(f"Failed to fetch events: {e}")
        if args.generate:
            html_content = generate_error_html(f"Failed to fetch events: {e}")
            out_path.write_text(html_content, encoding='utf-8')
        sys.exit(1)

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

        content = api.fetch_event_details(base_url, event_id, args.timeout)
        if content is None:
            logger.error(f"Could not fetch details for event {event_id}, skipping")
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
