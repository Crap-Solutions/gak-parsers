"""Graph generation for GAK ticket tracking."""

import io
import logging
import sqlite3
import datetime
import base64
import traceback
import dateutil.parser

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for cron
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


def generate_graph(db_path):
    """Generate sales graph with error handling. Returns base64-encoded PNG."""
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)

        # Import get_events_for_graph here to avoid circular import
        from .db import get_events_for_graph
        events = get_events_for_graph(conn)

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
        logger.error(f"Database error in generate_graph: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating graph: {e}")
        logger.debug(traceback.format_exc())
        return None
