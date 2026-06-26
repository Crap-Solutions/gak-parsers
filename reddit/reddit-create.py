#!/usr/bin/env python3
"""Update r/grazerak sidebar and gameplan sticky post.

Designed to run from cron. All network and Reddit-API operations are
guarded with timeouts and explicit error handling; failures are logged
and surfaced via the process exit code.
"""
import argparse
import logging
import os
import sys
import traceback

# gak_common lives at the repo root (one level up from this script).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gak_common.log import resolve_log_level, setup_logging

import requests
import jinja2
import praw
import prawcore
from datetime import datetime

# HTTP request timeout (seconds). Prevents a hung API from stalling cron.
REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


def get_table(url, timeout=REQUEST_TIMEOUT):
    """Fetch league table. Returns a list of {name, points} or None on error."""
    try:
        dataset = requests.get(url, timeout=timeout).json()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching table from {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch table from {url}: {e}")
        return None
    except ValueError as e:
        logger.error(f"Invalid JSON in table response from {url}: {e}")
        return None

    if not isinstance(dataset, list):
        logger.error(f"Expected list from table API, got {type(dataset).__name__}")
        return None

    table = []
    for ds in dataset:
        try:
            table.append({
                'name': ds['teamName'],
                'points': ds['points'],
            })
        except (KeyError, TypeError) as e:
            logger.warning(f"Malformed table entry {ds!r}: {e}; skipping")
            continue
    return table


def get_gameplan(url, timeout=REQUEST_TIMEOUT):
    """Fetch game plan. Returns a list of entries or None on error."""
    try:
        dataset = requests.get(url, timeout=timeout).json()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching gameplan from {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch gameplan from {url}: {e}")
        return None
    except ValueError as e:
        logger.error(f"Invalid JSON in gameplan response from {url}: {e}")
        return None

    if not isinstance(dataset, dict) or 'all' not in dataset:
        logger.error("Gameplan response missing expected 'all' field")
        return None
    # 'league' is indexed as dataset['league'][0]['league'] inside the loop to
    # tag league games. It's only reached when there are fixtures, so only
    # require it then -- an empty schedule ({"all": []}) is a valid "no games"
    # state, not an error.
    if dataset['all'] and not dataset.get('league'):
        logger.error("Gameplan response has fixtures but missing/empty 'league' field")
        return None

    gameplan = []
    for ds in dataset['all']:
        try:
            entry = {}
            entry['date'] = ds['datum']
            entry['time'] = ds['uhrzeit']
            entry['home'] = ds['heim']
            entry['away'] = ds['gast']
            if ds['heimTore'] is not None:
                entry['res'] = str(ds['heimTore']) + ':' + str(ds['gastTore'])
            entry['league'] = (ds['league'] == dataset['league'][0]['league'])

            if not entry.get('res'):
                entry['res'] = "-:-"
            else:
                a = ds['heimTore']
                b = ds['gastTore']
                res = ''
                if a == b:
                    res = 'D'
                elif entry['home'] == 'GAK 1902':
                    res = 'W' if a > b else 'L'
                else:
                    res = 'L' if a > b else 'W'
                entry['res'] = res + ' (' + entry['res'] + ')'

            gameplan.append(entry)
        except (KeyError, TypeError) as e:
            logger.warning(f"Malformed gameplan entry {ds!r}: {e}; skipping")
            continue
    return gameplan


def get_next_games(gameplan, limit=2):
    """Return the next `limit` games including today's."""
    next_games = []
    now = datetime.now().date()
    for e in gameplan:
        try:
            dt = datetime.strptime(e['date'], "%d.%m.%Y").date()
        except (ValueError, KeyError) as ex:
            logger.warning(f"Could not parse date {e.get('date')!r}: {ex}; skipping")
            continue
        if dt >= now:
            # show todays game too
            next_games.append(e)
        if len(next_games) >= limit:
            break
    return next_games


def pub_sidebar(subreddit, content):
    """Edit the sidebar wiki page."""
    subreddit.wiki["config/sidebar"].edit(content=content)


def update_gp_post(reddit, subreddit, title, content):
    """Find our existing sticky gameplan post or create one, then update it."""
    post = None
    for i in range(4):
        try:
            chk = subreddit.sticky(number=i)
            if chk.title == title and chk.author == reddit.user.me():
                post = chk
                break
        except prawcore.exceptions.NotFound:
            break
        except prawcore.exceptions.RequestException as e:
            logger.warning(f"Transient error while reading sticky {i}: {e}")
            continue
    if not post:
        post = subreddit.submit(title, selftext="placeholder")
    post.edit(content)
    post.mod.sticky(bottom=False, state=True)


def get_gp_title(gameplan):
    """Build the gameplan post title from the first/last match dates."""
    title = "Spielplan "
    title += datetime.strftime(datetime.strptime(gameplan[0]['date'],
                                                 "%d.%m.%Y"),
                               "%Y")
    title += datetime.strftime(datetime.strptime(gameplan[-1]['date'],
                                                 "%d.%m.%Y"),
                               "/%y")
    return title


def run(args):
    """Perform the update. Returns a process exit code."""
    reddit = praw.Reddit(user_agent="GAK mod")
    reddit.validate_on_submit = True
    subreddit = reddit.subreddit("grazerak")

    cur_path = os.path.dirname(os.path.abspath(__file__)) + '/'
    templ_path = cur_path + "templates/"
    os.makedirs(cur_path + "output", exist_ok=True)

    jenv = jinja2.Environment(loader=jinja2.FileSystemLoader(templ_path))
    sidebar_tmpl = jenv.get_template("sidebar.tmpl")
    gameplan_tmpl = jenv.get_template("gameplan.tmpl")
    timestamp = datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M")
    sidebar_tmpl.globals['timestamp'] = timestamp
    gameplan_tmpl.globals['timestamp'] = timestamp

    table_url = "https://www.grazerak.at/api/table/177"
    table = get_table(table_url, args.timeout)
    if table is None:
        logger.error("Aborting: could not retrieve league table")
        return 1

    gameplan_url = "https://www.grazerak.at/api/fixtures/0/1"
    gameplan = get_gameplan(gameplan_url, args.timeout)
    if gameplan is None:
        logger.error("Aborting: could not retrieve game plan")
        return 1

    if not gameplan:
        logger.warning("Game plan is empty; skipping gameplan post")
    next_games = get_next_games(gameplan) if gameplan else []

    # --- Sidebar ---
    content = sidebar_tmpl.render(table=table, next_games=next_games)
    with open(cur_path + "output/sidebar.txt", 'w') as f:
        f.write(content)
    try:
        pub_sidebar(subreddit, content)
    except prawcore.exceptions.PrawcoreException as e:
        logger.error(f"Failed to publish sidebar: {e}")
        return 1
    logger.info("Sidebar updated")

    # --- Gameplan sticky post ---
    if not gameplan:
        logger.info("No gameplan to post; done")
        return 0

    content = gameplan_tmpl.render(gameplan=gameplan)
    with open(cur_path + "output/gameplan.txt", 'w') as f:
        f.write(content)
    gp_title = get_gp_title(gameplan)
    try:
        update_gp_post(reddit, subreddit, gp_title, content)
    except prawcore.exceptions.PrawcoreException as e:
        logger.error(f"Failed to update gameplan post: {e}")
        return 1

    logger.info(f"Gameplan post '{gp_title}' updated")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Update r/grazerak sidebar and gameplan post')
    parser.add_argument('--timeout', type=int, default=REQUEST_TIMEOUT,
                        help=f'HTTP request timeout in seconds (default: {REQUEST_TIMEOUT})')
    parser.add_argument('--log', default=None,
                        help='Log file path (default: <scriptdir>/output/reddit-create.log)')
    parser.add_argument('--log-level', default=None,
                        help='Stdout log level (default: INFO). Also set via $GAK_LOG_LEVEL. '
                             'Use WARNING in cron to stay silent on success.')
    args = parser.parse_args()

    log_file = args.log or (os.path.dirname(os.path.abspath(__file__))
                            + '/output/reddit-create.log')
    setup_logging(log_file, stdout_level=resolve_log_level(args.log_level))

    try:
        return run(args)
    except Exception as e:
        logger.critical(f"Unhandled error: {e}\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
