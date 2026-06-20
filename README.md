# GAK Parsers

Collection of tools for GAK 1902 (Grazer Athletiksport-Klub).

## Tools

### Reddit Bot (reddit/)

Automated Reddit moderation for r/grazerak:

- **reddit-create.py**: Updates subreddit sidebar and game plan sticky posts
  - Fetches league table from 2liga.at
  - Fetches game schedule from grazerak.at
  - Uses Jinja2 templates for content rendering
  - Updates sidebar via PRAW API

**Usage:**
```bash
cd reddit
python reddit-create.py
```

**Requirements:**
- praw (Reddit API)
- requests
- jinja2

**Config:** Environment variables or `praw.ini` (see PRAW docs)

---

### Ticket Monitor (tickets/)

Tracks ticket sales for GAK events:

- **ticket-fetch.py**: Polls ticket.grazerak.at and tracks sales (runs from cron)
  - Fetches event and ticket availability data
  - Stores history in SQLite database (via the `lib/` package)
  - Generates matplotlib sales graphs
  - Renders an HTML report with Jinja2
  - Writes an error/empty-state page on failure so the site degrades gracefully

**Usage:**
```bash
cd tickets
# fetch only:
python ticket-fetch.py
# fetch and regenerate the HTML page (typical cron invocation):
python ticket-fetch.py --generate
```

Options: `--db`, `--output`, `--templates`, `--timeout`, `--log`, `--generate`.

**Database:** Stored in `tickets/data/ticket.db`

**Requirements:**
- requests
- jinja2
- matplotlib
- python-dateutil
- numpy

---

### Tippspiel Table (tippspiel/)

Calculates rankings for Tippspiel betting game:

- **tippspiel-table.py**: Reads Google Sheets and updates rankings
  - Reads player scores from Google Sheets
  - Calculates weighted ranking table
  - Updates summary sheet on Google

**Usage:**
```bash
cd tippspiel
python tippspiel-table.py
```

**Requirements:**
- google-api-python-client
- google-auth-oauthlib
- google-auth-httplib2

**Config:** Credentials stored in `~/.config/gak-parsers/`
- `credentials.json` (from Google Cloud Console)
- `token.json` (generated on first run)

---

## Configuration

Sensitive credentials are stored outside the repository:

```
~/.config/gak-parsers/
├── credentials.json  # Google Sheets OAuth
└── token.json      # Google Sheets refresh token
```

First run of tippspiel-table.py will prompt for OAuth authorization and generate `token.json`.

## Development

Install dependencies:

```bash
# Reddit
pip install praw requests jinja2

# Tickets
pip install requests jinja2 matplotlib python-dateutil

# Tippspiel
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```
