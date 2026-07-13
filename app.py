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
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
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

    for a in soup.select(link_selector):
        title = a.get_text(strip=True)
        href = a.get("href")
        if not title or not href or len(title) < 8:
            continue
        if not matches_keywords(title, source["keywords"]):
            continue
        if href.startswith("/"):
            href = urljoin(source["url"], href)
        results.append({"organization": source["name"], "title": title, "link": href, "source_url": source["url"]})

    unique = {}
    for r in results:
        unique[r["link"]] = r
    return list(unique.values())


# ---------- Email ----------

def send_email(new_items: list):
    from_addr = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]

    subject = f"🚨 {len(new_items)} New Electrical Engineering Vacancy(ies) in Nepal"
    body_lines = ["New Electrical Engineering Vacancies Detected\n"]
    for item in new_items:
        body_lines.append(
            f"Organization: {item['organization']}\n"
            f"Position: {item['title']}\n"
            f"Link: {item['link']}\n"
            f"Notice page: {item['source_url']}\n"
            f"{'-' * 40}"
        )
    body = "\n\n".join(body_lines)

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = from_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=context)
        server.login(from_addr, app_password)
        server.sendmail(from_addr, from_addr, msg.as_string())


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
            if vid not in seen:
                seen[vid] = {
                    "title": item["title"],
                    "link": item["link"],
                    "organization": item["organization"],
                    "first_seen": datetime.now().isoformat(timespec="seconds"),
                }
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
