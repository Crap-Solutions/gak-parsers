#!/usr/bin/env python3.11
import argparse
import io
from pathlib import Path
import sqlite3
import datetime
import base64
import requests
import jinja2
import dateutil.parser
import matplotlib.pyplot as plt


def init_db(db_file):
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


def update_db(conn, event, entry):
    conn.execute('''
    INSERT OR IGNORE INTO EVENTS
    (ID, TITLE, DATETIME, SELLFROM, SELLTO) VALUES
    (:id, :title, :dateTimeFrom, :publiclyAvailableFrom,
    :publiclyAvailableTo)
    ''', event)

    conn.execute('''
    INSERT INTO ENTRIES (MATCH, SOLD, AVAILABLE) VALUES
    (:id, :sold, :avail)''', entry)
    return


def draw_graph(db_path):
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    # select highest sold game
    # select last game
    # select future games
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

    for idx, entry in enumerate(events):
        event = {}
        event['id'] = entry[0]
        event['title'] = entry[1]
        event['time'] = dateutil.parser.parse(entry[2])
        event['sellfrom'] = dateutil.parser.parse(entry[3])
        event['sellto'] = dateutil.parser.parse(entry[4])
        events[idx] = event

    plt.style.use('tableau-colorblind10')
    plt.figure(figsize=(10, 6))
    plt.title("Ticket Sales Over Time")
    plt.xlabel("Hours Until Match")
    plt.ylabel("Tickets Sold (Online Available)")
    plt.grid(True, linestyle='--', alpha=0.7)

    for event in events:
        cur = conn.execute("SELECT * FROM ENTRIES WHERE MATCH=?",
                           (event['id'],))
        entries = cur.fetchall()
        hours = []
        sold = []
        for entry in entries:
            tickets = {}
            tickets['sold'] = entry[1]
            tickets['time'] = dateutil.parser.parse(entry[3])
            # it's actually stored in UTC
            tickets['time'] = tickets['time'].replace(
                    tzinfo=datetime.timezone.utc)
            h_diff = (event['time']-tickets['time']).total_seconds() / 3600
            tickets['diff'] = h_diff
            hours.append(h_diff)
            # cup correct - to delete:
            if event['id'] == "456e9a8a-ce64-4580-b9e0-3405a810c696":
                if tickets['sold'] >= 2302:
                    tickets['sold'] = tickets['sold'] - 1939
            # fix Horn at home - to delete:
            if event['id'] == "aeee2d94-edae-4a6d-a65c-f2ae274361ef":
                if tickets['sold'] >= 5100 and tickets['diff'] > 74.20:
                    tickets['sold'] = tickets['sold'] - 285
            # fix KSV at home - to delete:
            if event['id'] == "2e9e16ba-e8c3-409e-8c41-d7e6ddfaab40":
                # if tickets['sold'] >= 5400:
                    # tickets['sold'] = tickets['sold'] - 285
                if tickets['diff'] < 96.35:
                    tickets['sold'] = tickets['sold'] + 296 + 285
                    # if tickets['diff'] < 95:
                    #     tickets['sold'] = tickets['sold'] - 296
                    # if tickets['diff'] < 94.70:
                    #     tickets['sold'] = tickets['sold'] + 296
                    # if tickets['diff'] < 94.50:
                    #     tickets['sold'] = tickets['sold'] - 296
                if tickets['diff'] < 49.8:
                    tickets['sold'] = tickets['sold'] + 2333
            sold.append(tickets['sold'])

        plt.plot(hours, sold, label=event['title'])

    plt.gca().set_xlim([0, 600])
    plt.gca().invert_xaxis()
    plt.gca().xaxis.get_major_locator().set_params(integer=True)
    plt.legend(loc='upper left', bbox_to_anchor=(1, 1), fontsize='small')
    plt.tight_layout()
    tmpfile = io.BytesIO()
    plt.savefig(tmpfile, format='png')
    img = base64.b64encode(tmpfile.getvalue()).decode('utf-8')

    plt.close()
    conn.close()
    return img


def main():
    parser = argparse.ArgumentParser(description='Track GAK ticket sales and generate HTML report')
    parser.add_argument('--db', default='data/ticket.db',
                        help='Path to SQLite database (default: data/ticket.db)')
    parser.add_argument('--output', default='output/index.html',
                        help='Output HTML file path (default: output/index.html)')

    args = parser.parse_args()

    # Ensure data directory exists
    db_path = Path(args.db)
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure output directory exists
    out_path = Path(args.output)
    if not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = init_db(str(db_path))
    base_url = "https://ticket.grazerak.at/backend/events/"
    events_ep = "futurePublishedEvents"

    cur_path = Path(__file__).parent
    templ_path = cur_path / "templates"
    jenv = jinja2.Environment(loader=jinja2.FileSystemLoader(str(templ_path)))
    ticket_tmpl = jenv.get_template("ticket-html.tmpl")

    events = []
    for event in requests.get(base_url + events_ep).json():
        event_url = base_url + event["id"] + "/"
        chk_url = event_url + "public-stadium-representation-config"
        content = requests.get(chk_url).json()
        sold_cnt = 0
        avail_cnt = 0
        for entry in content['sectorRepresentationConfigurations']:
            for e in entry['seatConfigurations']:
                if e['seatStatus'] == 'SOLD':
                    sold_cnt += 1
                if e['seatStatus'] == 'AVAILABLE':
                    avail_cnt += 1
        entry = {}
        entry["title"] = event["title"]
        entry["id"] = event["id"]
        entry["sold"] = sold_cnt
        entry["avail"] = avail_cnt

        update_db(conn, event, entry)
        events.append(entry)

    conn.commit()
    conn.close()

    img = draw_graph(str(db_path))
    html_content = ticket_tmpl.render(events=events, img=img)

    # Write to output file
    out_path.write_text(html_content, encoding='utf-8')
    # print(f"Generated ticket report: {out_path}")

    return

    # 5619 (sections 15-25)
    # sector 14 624; usually never selling
    # 65 (VIP loge 1); likely sold out (?)
    # 60 (VIP loge 2); likely sold out (?)
    # 63 (VIP loge 3); likely sold out (?)
    # 65 (VIP loge 4); likely sold out (?)
    # 60 (VIP loge 5); likely sold out (?)
    # -581 (sector 24); usually not selling
    # -571 (sector 25); not selling
    # -416 (sector 15); not selling (Sponsors?)
    # -236 (sector 18); not selling (VIP?)
    # -156 (sector 18 VIP); sold out (?)
    # mx = 5619 - 581 - 571 - 416 - 236 - 156
    # vip_cnt = 65 + 60 + 63 + 65 + 60 + 156 + 236 = 705

    # sector 3 412
    # sector 4 286 + 21 + 25 + 24 = 356
    # sector 5 416 + 2* 16 + 2* 15 + 8 = 486

if __name__ == "__main__":
    main()
