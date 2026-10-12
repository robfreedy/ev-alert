# ev-alert

Watches the [CrazyNinjaOdds Positive EV table](https://crazyninjaodds.com/site/tools/positive-ev.aspx) and posts new bets at or above an EV threshold (3% by default) to a Discord channel. It's meant to run on a cron job.

## How it works

The [CrazyNinjaOdds API](https://api.crazyninjaodds.com/) only exposes the devigger, with no endpoint for the Positive EV feed. So the script reads the page directly:

1. **Load the page.** A `GET` on `positive-ev.aspx` returns an ASP.NET WebForms page. The table isn't in it yet, but the page carries the form state (`__VIEWSTATE`, `__EVENTVALIDATION`, and the default filter values).
2. **Replay the Update postback.** The script copies every form field, sets the **Minimum EV%** filter to `MIN_EV`, and `POST`s the form back as if the **Update** button was clicked. The server responds with the full page, results table included. The site filters by EV on its side.
3. **Parse the table.** It reads the `GridView1` table (columns: EV%, Date, Sport, League, Event, Market, Bet Name, Odds, Sportsbook, Fair Odds, Books). Start times come from the UTC timestamp inside each Date cell. Each row's EV is checked against the threshold again.
4. **Skip bets already sent.** Each bet is identified by *event + market + bet name + sportsbook + odds*. Bets alerted on within the last `DEDUP_HOURS` are stored in `seen.json` and skipped. A bet alerts again if its odds change or the window expires.
5. **Post to Discord.** New rows are sorted by EV, highest first, and sent to the webhook. Messages are split to stay under Discord's 2,000-character limit, and the script waits and retries when Discord rate-limits it (HTTP 429).

Example alert line:

```
**10.99%** | Chandler Simpson Over 1.5 +115 @ BetMGM (ON) (fair -107) | Player Hits + Runs + RBIs | Cleveland Guardians @ Tampa Bay Rays [MLB] | 10/13/2026 12:00:00 AM UTC
```

If the site changes its page layout and the table can't be found, the script exits with an error instead of quietly sending nothing.

## Setup

Requires Python 3.9+.

```bash
git clone git@github.com:robfreedy/ev-alert.git
cd ev-alert
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### Discord webhook

In Discord, go to **Channel settings → Integrations → Webhooks → New Webhook** and copy the URL. Then:

```bash
cp .env.example .env
# edit .env and paste the webhook URL
```

Anyone with the webhook URL can post to the channel. `.env` is gitignored, so never commit the URL. If it leaks, delete the webhook in Discord and create a new one.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | (required) | Discord webhook to post to |
| `MIN_EV` | `3` | Minimum EV percentage to alert on |
| `DEDUP_HOURS` | `24` | How long before the same bet/book/odds can alert again |
| `STATE_FILE` | `./seen.json` | Where already-alerted bets are stored |

### Test it

```bash
# Print matching rows without posting or saving state
./.venv/bin/python ev_alert.py --dry-run

# Post to Discord for real. The first run sends everything currently on the board.
. ./.env && ./.venv/bin/python ev_alert.py
```

## Running on a cron job

Open your crontab:

```bash
crontab -e
```

Add a line like this, using the absolute path to your clone. It runs every 5 minutes:

```cron
*/5 * * * * cd /path/to/ev-alert && . ./.env && ./.venv/bin/python ev_alert.py >> ev_alert.log 2>&1
```

Notes:

- **Use absolute paths.** Cron runs with a minimal environment and doesn't load your shell profile. That's why the line `cd`s into the repo and sources `.env` itself.
- **Pick an interval.** Odds move fast, so every 2–5 minutes works well. Duplicate bets are skipped, so running more often won't spam the channel. Please keep it reasonable so the site isn't hammered.
- **Logs.** Each run appends a line like `96 rows >= 3%, 4 new` to `ev_alert.log`, plus any errors. Check with `tail -f ev_alert.log`.
- **Sleep.** On a laptop, cron jobs don't run while the machine is asleep. For 24/7 alerts, run it on a server or an always-on machine.
- **macOS permissions.** If the job fails with "Operation not permitted", give `/usr/sbin/cron` Full Disk Access in **System Settings → Privacy & Security**. Or keep the repo outside protected folders like Desktop or Documents.

Confirm it's installed with `crontab -l`. To stop it, delete the line with `crontab -e`.
