#!/usr/bin/env python3
"""Scrape CrazyNinjaOdds Positive EV table and alert Discord on rows >= MIN_EV.

The public API (api.crazyninjaodds.com) only exposes the devigger, so this
replays the ASP.NET "Update" postback on the positive-ev page with the
Minimum EV% filter set, then parses the GridView.

Env:
  DISCORD_WEBHOOK_URL  required (unless --dry-run)
  MIN_EV               default 3 (percent)
  STATE_FILE           default ./seen.json next to this script
  DEDUP_HOURS          default 24 - don't re-alert the same bet/book/odds within this window
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://crazyninjaodds.com/site/tools/positive-ev.aspx"
P = "ctl00$ctl00$ContentPlaceHolderMain$ContentPlaceHolderRight$"
GRID_ID = "ContentPlaceHolderMain_ContentPlaceHolderRight_GridView1"

MIN_EV = float(os.environ.get("MIN_EV", "3"))
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
STATE_FILE = Path(os.environ.get("STATE_FILE", Path(__file__).with_name("seen.json")))
DEDUP_SECONDS = float(os.environ.get("DEDUP_HOURS", "24")) * 3600


def form_fields(form):
    data = {}
    for i in form.find_all("input"):
        name, typ = i.get("name"), i.get("type")
        if not name or typ in ("submit", "button", "image"):
            continue
        if typ == "checkbox":
            if i.has_attr("checked"):
                data[name] = "on"
            continue
        data[name] = i.get("value", "")
    for sel in form.find_all("select"):
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None:
            data[sel["name"]] = opt.get("value", "")
    return data


def fetch_rows():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (ev-alert script)"
    page = s.get(URL, timeout=30)
    page.raise_for_status()
    data = form_fields(BeautifulSoup(page.text, "html.parser").find("form", id="form1"))
    data[P + "ctl03$TextBoxMinimumEVPercentage"] = f"{MIN_EV:g}%"
    data[P + "ButtonUpdate"] = "Update"
    r = s.post(URL, data=data, timeout=60)
    r.raise_for_status()

    grid = BeautifulSoup(r.text, "html.parser").find(id=GRID_ID)
    if grid is None:
        raise RuntimeError("GridView not found in response - page layout may have changed")
    trs = grid.find_all("tr")
    if not trs:
        return []
    headers = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    rows = []
    for tr in trs[1:]:
        cells = tr.find_all("td")
        if len(cells) != len(headers):
            continue  # "no data" row, pager, etc.
        row = {h: c.get_text(" ", strip=True) for h, c in zip(headers, cells)}
        dt = cells[headers.index("Date")].find("datetime") if "Date" in headers else None
        row["Date"] = dt["utc"] + " UTC" if dt and dt.get("utc") else row.get("Date", "")
        ev_key = next(h for h in headers if "EV%" in h)
        try:
            row["_ev"] = float(row[ev_key].rstrip("%"))
        except ValueError:
            continue
        if row["_ev"] >= MIN_EV:  # server filters too; double-check
            rows.append(row)
    return rows


def row_key(r):
    return "|".join(r.get(k, "") for k in ("Event", "Market", "Bet Name", "Sportsbook", "Odds"))


def fmt(r):
    return (f"**{r['_ev']:.2f}%** | {r.get('Bet Name')} {r.get('Odds')} @ {r.get('Sportsbook')} "
            f"(fair {r.get('Fair Odds')}) | {r.get('Market')} | {r.get('Event')} "
            f"[{r.get('League')}] | {r.get('Date')}")


def post_discord(lines):
    # Discord content limit is 2000 chars; chunk lines into messages.
    header = f"🥷 **{len(lines)} new +EV bet(s) ≥ {MIN_EV:g}%** <{URL}>"
    chunks, cur = [], header
    for line in lines:
        if len(cur) + len(line) + 1 > 1990:
            chunks.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line[:1990]
    chunks.append(cur)
    for c in chunks:
        while True:
            resp = requests.post(WEBHOOK, json={"content": c}, timeout=30)
            if resp.status_code == 429:
                time.sleep(float(resp.json().get("retry_after", 1)))
                continue
            resp.raise_for_status()
            break


def main():
    dry = "--dry-run" in sys.argv
    if not dry and not WEBHOOK:
        sys.exit("DISCORD_WEBHOOK_URL not set")

    now = time.time()
    seen = {}
    if STATE_FILE.exists():
        try:
            seen = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    seen = {k: t for k, t in seen.items() if now - t < DEDUP_SECONDS}

    rows = sorted(fetch_rows(), key=lambda r: -r["_ev"])
    new = [r for r in rows if row_key(r) not in seen]
    print(f"{len(rows)} rows >= {MIN_EV:g}%, {len(new)} new")

    if new:
        lines = [fmt(r) for r in new]
        if dry:
            print("\n".join(lines))
        else:
            post_discord(lines)
    if not dry:
        for r in new:
            seen[row_key(r)] = now
        STATE_FILE.write_text(json.dumps(seen))


if __name__ == "__main__":
    main()
