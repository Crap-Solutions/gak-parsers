#!/usr/bin/env python3
"""Calculate Tippspiel rankings and write them back to Google Sheets.

Designed to run from cron. The interactive OAuth flow (which opens a
browser) is only attempted when a TTY is available; in a cron/non-interactive
environment an expired or missing token is reported and the job exits
non-zero instead of hanging forever.
"""
import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

# gak_common lives at the repo root (one level up from this script).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gak_common.log import resolve_log_level, setup_logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Config directory outside repo
CONFIG_DIR = Path.home() / ".config" / "gak-parsers"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# The ID and range of a sample spreadsheet.
SAMPLE_SPREADSHEET_ID = '18mhujvRfyFWSqTzGEnfpsYz4cPkajNeGVVAhwkhc_NI'
SAMPLE_RANGE_NAME = 'Tabelle!A2:E'


def load_credentials():
    """Load and refresh credentials.

    Returns valid Credentials, or None if (re)authorization is required.
    Exits the process when interactive authorization is impossible.
    """
    creds = None
    creds_path = CONFIG_DIR / 'token.json'
    if creds_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(creds_path), SCOPES)
        except ValueError as e:
            logger.warning(f"Stored token at {creds_path} is invalid: {e}; "
                           f"will re-authorize")
            creds = None

    if creds and creds.valid:
        return creds

    # Try a non-interactive refresh first.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, creds_path)
            return creds
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            creds = None  # fall through to interactive re-auth

    # Interactive authorization required.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        logger.error("No valid credentials and not running interactively; "
                     "cannot start OAuth flow from cron. Re-authorize manually.")
        sys.exit(2)

    client_secrets = CONFIG_DIR / 'credentials.json'
    if not client_secrets.exists():
        logger.error(f"Missing {client_secrets}; cannot authorize")
        sys.exit(2)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds, creds_path)
    return creds


def _save_token(creds, creds_path):
    try:
        with open(str(creds_path), 'w') as token:
            token.write(creds.to_json())
    except OSError as e:
        logger.error(f"Could not persist token to {creds_path}: {e}")


def parse_data(sdata):
    """Parse raw sheet values into per-round score dicts.

    Rows with an unexpected shape are skipped with a warning rather than
    crashing the whole run.
    """
    ret = []
    for s in sdata:
        entry = {}
        for e in s[1:]:
            try:
                tmp = {'score': e[4], 'obg': e[5]}
                entry[e[0]] = tmp
            except (IndexError, TypeError, KeyError) as ex:
                logger.warning(f"Skipping malformed tippspiel row {e!r}: {ex}")
                continue
        ret.append(entry)
    return ret


def get_players(results):
    players = set()
    for scores in results:
        players = players.union(set(scores.keys()))
    return list(players)


# Points awarded per scoring outcome. A score of '0' means the player
# participated but scored no points and is not counted toward any bucket.
# Single source of truth: get_table_data both counts into and totals from
# these keys, so the per-category weight and the total can no longer drift.
SCORE_WEIGHTS = {"1SC": 1, "FSC": 2, "2SC": 3, "FSC1": 6, "W": 12}


def get_table_data(results):
    table_data = []
    players = get_players(results)
    for p in players:
        scores = {k: 0 for k in SCORE_WEIGHTS}
        # name, played, scores-by-category, obg, total-points
        data = [p, 0, scores, 0, 0]
        for res in results:
            e = res.get(p, {})
            if not e:
                continue
            # Parse the tiebreaker first so a corrupt cell skips the whole
            # round (no partial increment to scores) instead of crashing the
            # run -- a single stray text value in the sheet used to kill cron.
            try:
                obg = int(e['obg'])
            except (ValueError, TypeError, KeyError) as ex:
                logger.warning(f"Invalid obg {e.get('obg')!r} for player "
                               f"{p!r}: {ex}; skipping round")
                continue
            score = e.get('score')
            if score in SCORE_WEIGHTS:
                scores[score] += 1
            elif score not in ('0', None):
                logger.warning(f"Unknown tippspiel score {score!r} for player "
                               f"{p!r}; skipping")
            data[-2] += obg
            data[1] += 1
        data[-1] = sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
        table_data.append(data)

    table_data.sort(key=lambda x: (-x[-1], x[-2], -x[1], x[0]))
    return table_data


def run():
    """Build the service, read rounds, compute and write the table.

    Returns a process exit code.
    """
    creds = load_credentials()

    try:
        service = build('sheets', 'v4', credentials=creds)
    except Exception as e:
        logger.error(f"Failed to build Sheets service: {e}")
        return 1

    # Call the Sheets API
    try:
        sheet = service.spreadsheets()
        sheet_info = sheet.get(spreadsheetId=SAMPLE_SPREADSHEET_ID).execute()
        sdata = []
        for s in sheet_info['sheets']:
            title = s['properties']['title']
            if title == 'Tabelle':
                continue
            res = sheet.values().get(spreadsheetId=SAMPLE_SPREADSHEET_ID,
                                     range=title+'!A:Z').execute()
            values = res.get('values', [])
            # only consider finished rounds (need at least 3 rows to compare)
            if len(values) < 3:
                continue
            # Heuristic gate for scored-vs-in-progress rounds: a round is
            # only counted once its first two data rows differ in length
            # (scoring appends a column). Carry-over behaviour with no test
            # coverage -- confirm against historical data before changing.
            if len(values[1]) == len(values[2]):
                continue
            sdata.append(values)
        results = parse_data(sdata)
        table_data = get_table_data(results)
        values = []
        for cnt, e in enumerate(table_data):
            values.append([cnt+1] + e[:2] + list(e[2].values()) + e[3:])
        range_name = 'Tabelle!A2' + ":J" + str(len(values)+1)
        sheet.values().update(spreadsheetId=SAMPLE_SPREADSHEET_ID,
                              range=range_name,
                              valueInputOption='USER_ENTERED',
                              body={'values': values}).execute()
        logger.info(f"Updated tippspiel table with {len(values)} player(s)")
        return 0

    except HttpError as err:
        logger.error(f"Google Sheets API error: {err}")
        return 1


def main():
    parser = argparse.ArgumentParser(description='Update Tippspiel ranking table in Google Sheets')
    parser.add_argument('--log', default=None,
                        help='Log file path (default: <scriptdir>/output/tippspiel.log)')
    parser.add_argument('--log-level', default=None,
                        help='Stdout log level (default: INFO). Also set via $GAK_LOG_LEVEL. '
                             'Use WARNING in cron to stay silent on success.')
    args = parser.parse_args()

    log_file = args.log or (os.path.dirname(os.path.abspath(__file__))
                            + '/output/tippspiel.log')
    setup_logging(log_file, stdout_level=resolve_log_level(args.log_level))

    try:
        return run()
    except Exception as e:
        logger.critical(f"Unhandled error: {e}\n{traceback.format_exc()}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
