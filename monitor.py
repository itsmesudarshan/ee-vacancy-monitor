#!/usr/bin/env python3
"""
Nepal Electrical Engineering Vacancy Monitor
----------------------------------------------
Checks configured job sources for new Electrical Engineering vacancies,
skips ones already seen, and emails a digest of new matches.

Run manually:      python3 monitor.py
Run via cron:       see README.md for the crontab line (every 12 hours)

Requires: requests, beautifulsoup4  (pip install -r requirements.txt)
"""

import json
import hashlib
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
SEEN_PATH = BASE_DIR / "seen_vacancies.json"
LOG_PATH = BASE_DIR / "monitor.log"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def vacancy_id(source_name: str, title: str, link: str) -> str:
    """Stable fingerprint so re-runs don't re-notify the same posting."""
    raw = f"{source_name}|{title.strip().lower()}|{link.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def matches_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def fetch_source(source: dict) -> list[dict]:
    """
    Generic scraper: pulls every <a> tag whose visible text looks like a
    job/vacancy link, keeps ones matching keywords.
    This is intentionally broad — job sites change their markup often, so
    precise per-site selectors will need occasional tuning (ask Claude Code
    to update the selector for a specific source when it breaks).
    """
    results = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"  ERROR fetching {source['name']}: {e}")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")

    # Optional CSS selector override per-source (set in config.json) for
    # sites where the generic <a> scrape is too noisy.
    link_selector = source.get("link_selector", "a")

    for a in soup.select(link_selector):
        title = a.get_text(strip=True)
        href = a.get("href")
        if not title or not href or len(title) < 8:
            continue
        if not matches_keywords(title, source["keywords"]):
            continue
        if href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(source["url"], href)
        results.append({
            "organization": source["name"],
            "title": title,
            "link": href,
            "source_url": source["url"],
        })

    # de-dup within this single fetch (same link can appear twice on a page)
    unique = {}
    for r in results:
        unique[r["link"]] = r
    return list(unique.values())


def send_email(new_items: list[dict], smtp_cfg: dict) -> None:
    subject = f"🚨 {len(new_items)} New Electrical Engineering Vacancy(ies) in Nepal"

    body_lines = ["New Electrical Engineering Vacancies Detected\n"]
    for item in new_items:
        body_lines.append(
            f"Organization: {item['organization']}\n"
            f"Position: {item['title']}\n"
            f"Link: {item['link']}\n"
            f"Notice page: {item['source_url']}\n"
            f"{'-'*40}"
        )
    body = "\n\n".join(body_lines)

    msg = MIMEMultipart()
    msg["From"] = smtp_cfg["from_addr"]
    msg["To"] = smtp_cfg["to_addr"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_cfg["smtp_host"], smtp_cfg["smtp_port"]) as server:
        server.starttls(context=context)
        server.login(smtp_cfg["from_addr"], smtp_cfg["app_password"])
        server.sendmail(smtp_cfg["from_addr"], smtp_cfg["to_addr"], msg.as_string())

    log(f"  Sent email with {len(new_items)} new vacancy(ies).")


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        log("ERROR: config.json not found. Copy config.example.json to config.json and edit it.")
        sys.exit(1)

    seen = load_json(SEEN_PATH, {})  # {vacancy_id: {title, link, first_seen}}
    new_items = []

    log("Starting monitor run...")
    for source in config["sources"]:
        log(f"Checking {source['name']} ({source['url']})")
        found = fetch_source(source)
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
        log(f"  {len(found)} matching link(s) found on this source.")

    if new_items:
        log(f"{len(new_items)} NEW vacancy(ies) found overall.")
        if config.get("smtp", {}).get("enabled", False):
            send_email(new_items, config["smtp"])
        else:
            log("  (Email sending disabled in config.json — printing instead)")
            for item in new_items:
                log(f"    NEW: {item['organization']} — {item['title']} — {item['link']}")
    else:
        log("No new vacancies this run.")

    save_json(SEEN_PATH, seen)
    log("Run complete.\n")


if __name__ == "__main__":
    main()
