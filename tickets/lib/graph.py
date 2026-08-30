"""Graph generation for GAK ticket tracking."""

import io
import logging
import sqlite3
import datetime
import base64
import traceback
import dateutil.parser
import numpy as np

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for cron
import matplotlib.pyplot  # used as matplotlib.pyplot.* throughout; no alias

logger = logging.getLogger(__name__)


def generate_graph(db_path):
    """Generate sales graph with error handling. Returns base64-encoded PNG."""
    conn = None
    try:
        # Import here to avoid a circular import.
        from .db import (open_connection, get_entries_for_event,
                         get_events_for_graph)
        conn = open_connection(db_path, read_only=True)
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

        # Collect final sales numbers for average calculation
        final_sales = []

        # "Now" as naive UTC for the past/future classification, matching the
        # normalisation convention in ticket-fetch.py (API datetimes are made
        # UTC-aware, then naive) so an offset like +02:00 doesn't skew it.
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        for event in parsed_events:
            # The average line must reflect *final* attendance figures, which
            # only past events have -- future events' tallies are still
            # climbing, so including them would drag the average down.
            event_time = event['time']
            if event_time.tzinfo is not None:
                event_time = event_time.astimezone(datetime.timezone.utc)
            is_past = event_time.replace(tzinfo=None) < now

            try:
                # Ordered oldest-first by db.get_entries_for_event, so sold[-1]
                # below is genuinely the latest sample.
                entries = get_entries_for_event(conn, event['id'])

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

                        hours.append(h_diff)
                        sold.append(tickets['sold'])

                    except (dateutil.parser.ParserError, ValueError, KeyError) as e:
                        logger.warning(f"Error parsing entry for event {event['id']}: {e}")
                        continue

                if hours and sold:
                    matplotlib.pyplot.plot(hours, sold, label=event['title'])
                    # Collect final sales number (last entry) -- past events
                    # only, see is_past above.
                    if is_past:
                        final_sales.append(sold[-1])

            except sqlite3.Error as e:
                logger.error(f"Database error while fetching entries for {event['id']}: {e}")
                continue

        # Calculate and plot average line (horizontal)
        if final_sales:
            avg_sales = np.mean(final_sales)
            matplotlib.pyplot.axhline(y=avg_sales, color='black', linestyle='--',
                                     linewidth=1, label=f'Average ({int(avg_sales)})', alpha=0.4)

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

        return img

    except sqlite3.Error as e:
        logger.error(f"Database error in generate_graph: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating graph: {e}")
        logger.debug(traceback.format_exc())
        return None
    finally:
        # Close on every path (incl. the early `return None` branches and
        # exceptions); previously the connection only closed on success.
        # Also release the matplotlib figure, which used to leak on every
        # non-success path (early returns + exceptions).
        matplotlib.pyplot.close('all')
        if conn is not None:
            conn.close()
