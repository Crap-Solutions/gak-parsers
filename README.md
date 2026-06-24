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
  - Suppresses the cron alert email for a configurable grace window after a
    fetch failure, because the upstream server has regular short downtimes and
    cron polls every 5 minutes (see below)

**Usage:**
```bash
cd tickets
# fetch only:
python ticket-fetch.py
# fetch and regenerate the HTML page (typical cron invocation):
python ticket-fetch.py --generate
```

Options: `--db`, `--output`, `--templates`, `--timeout`, `--log`, `--log-level`,
`--alert-grace`, `--failstate`, `--generate`.

**Cron / alerting:** the job runs every 5 minutes and cron mails any stdout
output. Two knobs keep the inbox quiet:

- `--log-level` / `$GAK_LOG_LEVEL` — stdout level. Use `WARNING` in cron so a
  successful run is silent (the file log still keeps `INFO`).
- `--alert-grace` / `$GAK_ALERT_GRACE` — seconds to suppress the alert email
  after a fetch failure *starts* (default 24h; `0` disables it). The first
  failure of an outage is recorded to a state file (`<db>.failstate`, override
  with `--failstate`) and logged to the file only; the last good page keeps
  being served. Only once the outage has persisted past the window does the
  job print the error to stdout (-> cron email), replace the page, and exit 1.
  A successful fetch clears the state. So a typical blip produces no email; a
  multi-hour/multi-day downtime produces exactly one alert.

A typical cron line therefore needs no special flags beyond what's above:

```cron
*/5 * * * * GAK_LOG_LEVEL=WARNING python3 ticket-fetch.py \
    --db data/events.db --output /var/www/.../index.html \
    --log /var/log/ticket-fetch.log --generate
```

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

Install dependencies (the repo root `requirements.txt` is the union of
every tool's deps, used by CI and needed to run the test suite, which
imports all three scripts):

```bash
pip install -r requirements.txt
```

For a production deployment of a single tool, install only its subset:

```bash
# Reddit
pip install praw prawcore requests jinja2

# Tickets
pip install requests jinja2 matplotlib python-dateutil numpy

# Tippspiel
pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

Run the tests:

```bash
python -m pytest -q
```
