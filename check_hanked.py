#!/usr/bin/env python3
"""
Daily Riigihanked monitor for a large medical laboratory.
Fetches procurements published in the last 24h and emails relevant ones.
"""
import os
import json
import time
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

API_URL = "https://riigihanked.riik.ee/rhr/api/public/v1/search/procurements"
BASE_URL = "https://riigihanked.riik.ee/rhr-web/#/procurement/{}/general-info"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

GEMINI_PROMPT = """You are evaluating Estonian public procurement notices for relevance to a large medical laboratory services company.

INCLUDE procurements about:
- Laboratory testing services or clinical analysis services
- Occupational health screening or employee health checks (töötervishoiuteenused)
- Medical laboratory work: microbiological, histological, cytological, biochemical, serological analyses
- Blood, urine, or other biological sample analysis
- PCR, rapid testing, or infectious disease screening services
- General health screening or diagnostic testing services

EXCLUDE procurements about:
- Medical devices or equipment (meditsiiniseadmed)
- Reagents, chemicals, or laboratory consumables (reagendid, laboritarbed)
- Medical software or IT systems
- Construction, renovation, or facility maintenance
- Pharmaceuticals or drugs
- Ambulance, transport, or logistics services
- Radiology equipment or imaging devices

Here are the procurements as JSON. Each has a reference number, name, CPV category, and short description:
{items}

Return ONLY a raw JSON array of reference numbers (strings) that should be INCLUDED. No explanation, no markdown.
Example: ["311400", "311317"]
If none qualify, return: []"""


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

    if isinstance(data, list):
        return data
    for key in ("procurements", "data", "content", "items", "results"):
        if key in data:
            return data[key]
    return []


def filter_with_groq(procurements: list, api_key: str) -> list:
    items = [
        {
            "ref": p.get("procurementReferenceNr", ""),
            "name": p.get("procurementName", ""),
            "category": p.get("mainCpvName", ""),
            "description": p.get("shortDescription", ""),
        }
        for p in procurements
    ]

    prompt = GEMINI_PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=2))

    for attempt in range(3):
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=30,
        )
        if response.status_code == 429:
            wait = 40 * (attempt + 1)
            print(f"Rate limited, waiting {wait}s before retry {attempt + 1}/3...")
            time.sleep(wait)
            continue
        if not response.ok:
            print(f"Groq error {response.status_code}: {response.text}")
        response.raise_for_status()
        break

    raw = response.json()["choices"][0]["message"]["content"].strip()
    print(f"Groq response: {raw}")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    relevant_refs = set(json.loads(raw))
    return [p for p in procurements if p.get("procurementReferenceNr") in relevant_refs]


def format_procurement(p: dict) -> str:
    name = p.get("procurementName") or "Pealkiri puudub"
    ref = p.get("procurementReferenceNr") or ""
    buyer = p.get("contractingAuthorityName") or ""
    date_raw = p.get("procProcessRevealDate") or ""
    date = date_raw[:10] if date_raw else ""
    cpv = p.get("mainCpvName") or ""
    url = BASE_URL.format(ref) if ref else "https://riigihanked.riik.ee/rhr-web/#/search"

    lines = [f"• {name}"]
    if cpv:
        lines.append(f"  Kategooria: {cpv}")
    if buyer:
        lines.append(f"  Hankija: {buyer}")
    if date:
        lines.append(f"  Avaldatud: {date}")
    lines.append(f"  Link: {url}")
    return "\n".join(lines)


def send_email(relevant: list, total: int, gmail_user: str, gmail_password: str, recipient: str):
    today = datetime.now().strftime("%d.%m.%Y")

    if not relevant:
        subject = f"[Riigihanked {today}] the clientile sobivaid hankeid ei leitud"
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total}\n\n"
            "Viimase 24 tunni jooksul ei leitud a large medical laboratory-le potentsiaalselt sobivaid riigihanked.\n\n"
            f"Kõik täna avaldatud hanked: https://riigihanked.riik.ee/rhr-web/#/search"
        )
    else:
        subject = f"[Riigihanked {today}] {len(relevant)} potentsiaalselt sobivat hanget the clientile"
        items = "\n\n".join(format_procurement(p) for p in relevant)
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total} | Sobivaid: {len(relevant)}\n\n"
            "Potentsiaalselt a large medical laboratory-le sobivad riigihanked:\n\n"
            f"{items}\n\n"
            f"Kõik täna avaldatud hanked: https://riigihanked.riik.ee/rhr-web/#/search"
        )

    recipients = [r.strip() for r in recipient.split(",")]

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipients, msg.as_string())

    print(f"Email sent to {', '.join(recipients)}")


def main():
    now = datetime.now(timezone.utc)
    date_to = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_from = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"Fetching procurements from {date_from} to {date_to}...")
    procurements = fetch_procurements(date_from, date_to)
    print(f"Total procurements: {len(procurements)}")

    gemini_api_key = os.environ["GROQ_API_KEY"]
    relevant = filter_with_groq(procurements, gemini_api_key)
    print(f"Relevant to the client: {len(relevant)}")

    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    send_email(relevant, len(procurements), gmail_user, gmail_password, recipient)


if __name__ == "__main__":
    main()
