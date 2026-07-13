# Nepal EE Vacancy Monitor — Setup Guide

This is a script version of the "monitor Nepal for Electrical Engineering
vacancies and email me" idea. It's built to run inside **Claude Code**
(or any machine with Python 3 + cron), not inside a chat session — chats
don't stay running in the background.

## 1. Install

```bash
cd ee-vacancy-monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure

```bash
cp config.example.json config.json
```

Edit `config.json`:
- Add/remove `sources` (job sites, NEA notices page, specific hydropower
  company career pages, etc). Each source needs a `url` and a list of
  `keywords` to match against link text.
- Some sites render job listings with JavaScript, so a plain `requests`
  fetch won't see them — if a source you care about returns 0 results,
  tell Claude Code "this source isn't matching, help me fix the selector"
  and it can inspect the page and adjust `link_selector` or switch that
  source to a headless-browser fetch (e.g. via `playwright`).

## 3. Gmail setup (app password, not your real password)

Gmail blocks plain password login for scripts. Use an **App Password**:

1. Turn on 2-Step Verification on the Gmail account: https://myaccount.google.com/security
2. Go to https://myaccount.google.com/apppasswords
3. Generate a password for "Mail" / "Other (custom name)"
4. Paste the 16-character password into `config.json` as `app_password`
5. Set `"enabled": true` in the `smtp` block

Without this, the script still runs and just logs new vacancies to
`monitor.log` and stdout instead of emailing — useful for testing first.

## 4. Test a run

```bash
python3 monitor.py
```

Check `monitor.log`. First run will report everything found as "new"
since `seen_vacancies.json` starts empty — that's expected. Run it a
second time immediately; it should report 0 new (dedup working).

## 5. Schedule it (every 12 hours)

```bash
crontab -e
```

Add:

```
0 6,18 * * * cd /full/path/to/ee-vacancy-monitor && ./venv/bin/python3 monitor.py >> cron.log 2>&1
```

This runs at 6 AM and 6 PM server time. Adjust the hours to your timezone
(Nepal Time is UTC+5:45 — if the server is in UTC, offset accordingly).

## 6. Files this creates

- `seen_vacancies.json` — dedup history, don't delete unless you want
  every current listing re-reported as "new"
- `monitor.log` — human-readable run log

## Known limitations (be upfront about these)

- This is a keyword-matching link scraper, not a true structured extractor.
  It won't reliably pull salary, deadline, or qualification fields the way
  the original spec wanted — job sites don't expose that consistently in
  raw HTML without per-site parsing work.
- Sites that require JavaScript rendering (many modern job boards) need
  `playwright` instead of plain `requests` — ask Claude Code to add that
  for a specific source if it's coming back empty.
- No government PSC/Lok Sewa scraper included yet — that site's structure
  needs its own selector; add it as a source once you check its HTML.
