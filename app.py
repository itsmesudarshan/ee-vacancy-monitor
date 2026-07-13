#!/usr/bin/env python3
"""
Nepal Electrical Engineering Vacancy Monitor — Web Service version
---------------------------------------------------------------------
Deploy as a Render Web Service (free tier). cron-job.org calls
GET /run-check?token=YOUR_SECRET every 12 hours to trigger a check.

Dedup history (seen_vacancies.json) is stored inside your GitHub repo
via the GitHub Contents API, so it survives Render restarts/free-tier
disk wipes.

Required environment variables (set in Render dashboard, NOT in code):
  TRIGGER_SECRET      - random string; cron-job.org must send this as ?token=
  GITHUB_TOKEN         - GitHub personal access token (repo scope)
  GITHUB_REPO          - "yourusername/ee-vacancy-monitor"
  GMAIL_ADDRESS         - your gmail address (sender AND recipient)
  GMAIL_APP_PASSWORD    - 16-char Gmail app password
"""

import os
import json
import base64
import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NEPAL_TZ = ZoneInfo("Asia/Kathmandu")


def today_nepal():
    """Current date in Nepal time (UTC+5:45) — server runs in UTC on Render,
    so comparisons must use this instead of datetime.now().date() to avoid
    off-by-one errors near midnight."""
    return datetime.now(NEPAL_TZ).date()
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

GITHUB_API = "https://api.github.com"
SEEN_FILE_PATH = "seen_vacancies.json"  # path inside the repo


# ---------- GitHub-backed persistence ----------

def _github_headers():
    token = os.environ["GITHUB_TOKEN"]
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def load_seen():
    repo = os.environ["GITHUB_REPO"]
    url = f"{GITHUB_API}/repos/{repo}/contents/{SEEN_FILE_PATH}"
    resp = requests.get(url, headers=_github_headers(), timeout=15)
    if resp.status_code == 404:
        return {}, None
    resp.raise_for_status()
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def save_seen(seen: dict, sha: str | None):
    repo = os.environ["GITHUB_REPO"]
    url = f"{GITHUB_API}/repos/{repo}/contents/{SEEN_FILE_PATH}"
    body = {
        "message": f"Update seen vacancies - {datetime.now().isoformat(timespec='seconds')}",
        "content": base64.b64encode(json.dumps(seen, indent=2, ensure_ascii=False).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    resp = requests.put(url, headers=_github_headers(), json=body, timeout=15)
    resp.raise_for_status()


# ---------- Config (sources/keywords - not secret, lives in repo) ----------

def load_config():
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- Scraping ----------

def matches_keywords(text: str, keywords: list) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


MONTH_MAP = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def extract_posting_date(title: str):
    """
    Best-effort extraction of a publish date embedded in listing titles.
    Handles patterns seen on collegenp.com etc:
      - suffix ISO date:      "...Vacancy2025-12-01"  -> 2025-12-01
      - prefix DDMonYYYY:     "01Dec2025Tamor..."     -> 2025-12-01
    Returns a datetime.date, or None if no recognizable date found
    (in which case the item is kept — we only filter out things we're
    confident are old, not things we're unsure about).
    """
    # suffix ISO: YYYY-MM-DD at the very end of the string
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})\s*$", title)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass

    # prefix DDMonYYYY at the very start of the string
    m = re.match(r"^(\d{2})(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{4})", title)
    if m:
        day, mon, year = int(m.group(1)), MONTH_MAP[m.group(2)], int(m.group(3))
        try:
            return datetime(year, mon, day).date()
        except ValueError:
            pass

    return None


FULL_MONTH_MAP = {name: i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}

DEADLINE_KEYWORDS = [
    "application deadline", "last date", "closing date", "apply before",
    "deadline", "last day to apply", "vacancy closes", "application closes",
]


def _try_parse_date_token(token: str):
    token = token.strip().strip(".,")
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", token)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", token)
    if m:
        day, mon, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, mon, day).date()
        except ValueError:
            return None
    # "15 January 2026" or "January 15, 2026"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", token)
    if m and m.group(2) in FULL_MONTH_MAP:
        try:
            return datetime(int(m.group(3)), FULL_MONTH_MAP[m.group(2)], int(m.group(1))).date()
        except ValueError:
            return None
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", token)
    if m and m.group(1) in FULL_MONTH_MAP:
        try:
            return datetime(int(m.group(3)), FULL_MONTH_MAP[m.group(1)], int(m.group(2))).date()
        except ValueError:
            return None
    return None


def extract_deadline_from_page(html_text: str):
    """
    Best-effort: find a deadline-style keyword, then look for a date token
    within the following ~80 characters. Returns a date or None if no
    deadline could be confidently found (caller keeps the item in that case
    — we only filter out vacancies we're SURE have expired).
    """
    text = re.sub(r"\s+", " ", html_text)
    text_lower = text.lower()

    date_token_pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
        r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})"
    )

    for kw in DEADLINE_KEYWORDS:
        idx = text_lower.find(kw)
        if idx == -1:
            continue
        window = text[idx: idx + len(kw) + 80]
        m = date_token_pattern.search(window)
        if m:
            parsed = _try_parse_date_token(m.group(1))
            if parsed:
                return parsed
    return None


def fetch_deadline(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Could not fetch detail page for deadline check ({url}): {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    return extract_deadline_from_page(soup.get_text(" "))


def vacancy_id(source_name: str, title: str, link: str) -> str:
    raw = f"{source_name}|{title.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_source(source: dict) -> list:
    results = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR fetching {source['name']}: {e}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    link_selector = source.get("link_selector", "a")
    max_age_days = source.get("max_age_days", 45)
    cutoff = today_nepal() - timedelta(days=max_age_days)

    for a in soup.select(link_selector):
        title = a.get_text(strip=True)
        href = a.get("href")
        if not title or not href or len(title) < 8:
            continue
        if not matches_keywords(title, source["keywords"]):
            continue

        posting_date = extract_posting_date(title)
        if posting_date is not None and posting_date < cutoff:
            continue  # too old, skip — confident it's a backlog listing

        if href.startswith("/"):
            href = urljoin(source["url"], href)
        results.append({
            "organization": source["name"],
            "title": title,
            "link": href,
            "source_url": source["url"],
            "posting_date": posting_date.isoformat() if posting_date else None,
        })

    unique = {}
    for r in results:
        unique[r["link"]] = r
    return list(unique.values())


# ---------- Email (via Resend HTTPS API — Render free tier blocks raw SMTP ports) ----------

def send_email(new_items: list):
    to_addr = os.environ["GMAIL_ADDRESS"]
    api_key = os.environ["RESEND_API_KEY"]

    subject = f"🚨 {len(new_items)} New Electrical Engineering Vacancy(ies) in Nepal"
    body_lines = ["New Electrical Engineering Vacancies Detected", ""]
    for item in new_items:
        body_lines.append(
            f"Organization: {item['organization']}\n"
            f"Position: {item['title']}\n"
            f"Deadline: {item.get('deadline', 'Not specified')}\n"
            f"Link: {item['link']}\n"
            f"Notice page: {item['source_url']}\n"
            f"{'-' * 40}"
        )
    body_text = "\n\n".join(body_lines)
    body_html = "<br><br>".join(line.replace("\n", "<br>") for line in body_lines)

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": "EE Vacancy Monitor <onboarding@resend.dev>",
            "to": [to_addr],
            "subject": subject,
            "text": body_text,
            "html": body_html,
        },
        timeout=20,
    )
    resp.raise_for_status()


# ---------- Core run ----------

def run_check() -> dict:
    config = load_config()
    seen, sha = load_seen()
    new_items = []
    per_source_counts = {}

    for source in config["sources"]:
        found = fetch_source(source)
        per_source_counts[source["name"]] = len(found)
        for item in found:
            vid = vacancy_id(source["name"], item["title"], item["link"])
            if vid in seen:
                continue

            # Only now, for genuinely new candidates, fetch the detail page
            # to check if the deadline has already passed. Keeps the item
            # if no deadline could be confidently determined.
            deadline = fetch_deadline(item["link"])
            item["deadline"] = deadline.isoformat() if deadline else "Not specified"

            seen[vid] = {
                "title": item["title"],
                "link": item["link"],
                "organization": item["organization"],
                "first_seen": datetime.now().isoformat(timespec="seconds"),
                "deadline": item["deadline"],
            }

            if deadline is not None and deadline < today_nepal():
                continue  # expired — mark as seen (above) but don't alert

            new_items.append(item)

    if new_items:
        send_email(new_items)

    save_seen(seen, sha)

    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "new_vacancies_found": len(new_items),
        "new_items": new_items,
        "per_source_matches": per_source_counts,
    }


# ---------- Routes ----------

@app.route("/")
def health():
    return "EE Vacancy Monitor is running.", 200


@app.route("/run-check")
def trigger():
    token = request.args.get("token")
    if token != os.environ.get("TRIGGER_SECRET"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        result = run_check()
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
