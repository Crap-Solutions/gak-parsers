#!/usr/bin/env python3.11
from __future__ import print_function

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Config directory outside repo
CONFIG_DIR = Path.home() / ".config" / "gak-parsers"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# The ID and range of a sample spreadsheet.
SAMPLE_SPREADSHEET_ID = '18mhujvRfyFWSqTzGEnfpsYz4cPkajNeGVVAhwkhc_NI'
SAMPLE_RANGE_NAME = 'Tabelle!A2:E'


def parse_data(sdata):
    ret = []
    for s in sdata:
        entry = {}
        for e in s[1:]:
            tmp = {}
            tmp['score'] = e[4]
            tmp['obg'] = e[5]
            entry[e[0]] = tmp
        ret.append(entry)
    return ret


def get_players(results):
    players = set()
    for scores in results:
        players = players.union(set(scores.keys()))
    return list(players)


def get_table_data(results):
    table_data = []
    players = get_players(results)
    for p in players:
        # name, played, scores, obg, score
        data = [p, 0, {}, 0, 0]
        scores = {"1SC": 0, "FSC": 0, "2SC": 0, "FSC1": 0, "W": 0}
        for res in results:
            e = res.get(p, {})
            if not e:
                continue
            score = e['score']
            if score != '0':
                scores[e['score']] += 1
            data[-2] += int(e['obg'])
            data[1] += 1
        data[2] = scores
        data[-1] = scores["1SC"] + scores["FSC"]*2 + scores["2SC"]*3 + \
            scores["FSC1"]*6 + scores["W"]*12
        table_data.append(data)

    table_data.sort(key=lambda x: (-x[-1], x[-2], -x[1], x[0]))
    return table_data


def main():
    """Shows basic usage of the Sheets API.
    Prints values from a sample spreadsheet.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    creds_path = CONFIG_DIR / 'token.json'
    if creds_path.exists():
        creds = Credentials.from_authorized_user_file(str(creds_path), SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CONFIG_DIR / 'credentials.json'), SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(str(creds_path), 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('sheets', 'v4', credentials=creds)

        # Call the Sheets API
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
            # only consider finished rounds
            if len(values) < 2 or len(values[1]) == len(values[2]):
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

    except HttpError as err:
        print(err)


if __name__ == '__main__':
    main()
