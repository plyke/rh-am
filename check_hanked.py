#!/usr/bin/env python3
"""
Daily Riigihanked monitor for the client Eesti OÜ.
Fetches procurements published in the last 24h and emails relevant ones.
"""
import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

API_URL = "https://riigihanked.riik.ee/rhr/api/public/v1/search/procurements"
BASE_URL = "https://riigihanked.riik.ee/rhr-web/#/procurement/{}/general-info"

# Keywords relevant to the client: lab testing, diagnostics, occupational health, imaging
RELEVANT_KEYWORDS = [
    # Core lab / testing
    "labor", "labori", "laboratoor", "analüüs", "analüüse", "analüüside",
    "uuring", "uuringud", "uuringute", "uuringuteenused",
    "diagnostika", "diagnostiline", "diagnoos",
    # Occupational health
    "töötervishoiu", "töötervishoiuteenused", "töötervishoiuteenus",
    "tervisekontroll", "tervisekontrolli", "töötervishoid",
    # Medical specialties the client covers
    "mikrobioloogia", "histoloogia", "tsütoloogia", "patoloogia",
    "biokeemia", "seroloogia", "geneetika", "immunoloogia",
    "vereu", "vereanalüüs", "vereanalüüside",
    "radioloogia", "pildidiagnostika", "röntgen", "ultraheliuuring",
    # General health services
    "meditsiini", "meditsiinilise", "tervishoiuteenus", "kliiniline",
    "haigla", "polikliinik", "ambulatoor",
    # Testing / screening
    "pcr", "kiirtest", "kiirtestid", "sõeluuring", "ennetav",
    "vaktsineerimine", "vaktsineerimis",
    # Lab supplies (adjacent)
    "reaktiiv", "reaktiivid", "laborivarustus", "laboritarbed",
    "meditsiiniseadmed", "meditsiinivarustus",
    # English terms sometimes appear
    "laboratory", "diagnostic", "medical testing", "health screening",
    "clinical analysis",
]

def fetch_procurements(date_from: str, date_to: str) -> list:
    payload = {
        "orderBy": {"procurementProcessRevealDate": "desc"},
        "filter": {
            "procurementProcessRevealDateBegin": date_from,
            "procurementProcessRevealDateEnd": date_to,
        },
    }

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://riigihanked.riik.ee",
        "Referer": "https://riigihanked.riik.ee/rhr-web/",
    })

    # Establish session via the API itself to get XSRF token
    session.get("https://riigihanked.riik.ee/rhr/api/public/v1/current-user", timeout=30)
    xsrf_token = session.cookies.get("XSRF-TOKEN")
    if xsrf_token:
        session.headers["X-XSRF-TOKEN"] = xsrf_token
        print(f"XSRF token obtained: {xsrf_token[:8]}...")
    else:
        print("Warning: no XSRF token received")

    response = session.post(API_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Handle different possible response shapes
    if isinstance(data, list):
        return data
    for key in ("procurements", "data", "content", "items", "results"):
        if key in data:
            return data[key]
    return []

def is_relevant(procurement: dict) -> bool:
    text = json.dumps(procurement, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in RELEVANT_KEYWORDS)

def format_procurement(p: dict) -> str:
    # Try common field name variants
    name = (
        p.get("procurementObjectName")
        or p.get("name")
        or p.get("title")
        or "Pealkiri puudub"
    )
    ref = (
        p.get("procurementNumber")
        or p.get("referenceNumber")
        or p.get("id")
        or ""
    )
    buyer = (
        p.get("procuringEntityName")
        or p.get("buyerName")
        or p.get("organisationName")
        or ""
    )
    date_raw = (
        p.get("procurementProcessRevealDate")
        or p.get("publishedDate")
        or p.get("revealDate")
        or ""
    )
    date = date_raw[:10] if date_raw else ""
    url = BASE_URL.format(ref) if ref else "https://riigihanked.riik.ee/rhr-web/#/search"

    lines = [f"• {name}"]
    if buyer:
        lines.append(f"  Hankija: {buyer}")
    if date:
        lines.append(f"  Avaldatud: {date}")
    if ref:
        lines.append(f"  Viitenumber: {ref}")
    lines.append(f"  Link: {url}")
    return "\n".join(lines)

def send_email(relevant: list, total: int, gmail_user: str, gmail_password: str, recipient: str):
    today = datetime.now().strftime("%d.%m.%Y")

    if not relevant:
        subject = f"[Riigihanked {today}] the clientile sobivaid hankeid ei leitud"
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total}\n\n"
            "Viimase 24 tunni jooksul ei leitud the client Eesti OÜ-le potentsiaalselt sobivaid riigihanked.\n\n"
            f"Kõik täna avaldatud hanked: https://riigihanked.riik.ee/rhr-web/#/search"
        )
    else:
        subject = f"[Riigihanked {today}] {len(relevant)} potentsiaalselt sobivat hanget the clientile"
        items = "\n\n".join(format_procurement(p) for p in relevant)
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total} | Sobivaid: {len(relevant)}\n\n"
            "Potentsiaalselt the client Eesti OÜ-le sobivad riigihanked:\n\n"
            f"{items}\n\n"
            f"Kõik täna avaldatud hanked: https://riigihanked.riik.ee/rhr-web/#/search"
        )

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipient, msg.as_string())

    print(f"Email sent to {recipient}")

def main():
    now = datetime.now(timezone.utc)
    date_to = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_from = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"Fetching procurements from {date_from} to {date_to}...")
    procurements = fetch_procurements(date_from, date_to)
    print(f"Total procurements: {len(procurements)}")
    if procurements:
        print(f"First procurement keys: {list(procurements[0].keys())}")
        print(f"First procurement sample: {json.dumps(procurements[0], ensure_ascii=False, indent=2)[:800]}")

    relevant = [p for p in procurements if is_relevant(p)]
    print(f"Relevant to the client: {len(relevant)}")

    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    send_email(relevant, len(procurements), gmail_user, gmail_password, recipient)

if __name__ == "__main__":
    main()
