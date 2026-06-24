#!/usr/bin/env python3
"""
Weekly Riigihanked monitor for two analysts looking for hobby project work.
Fetches procurements published in the last 7 days and emails relevant ones.
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
BASE_URL = "https://riigihanked.riik.ee/rhr-web/#/procurement-ref/{}"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

GEMINI_PROMPT = """You are evaluating Estonian public procurement notices for relevance to two freelance analysts (one data/BI analyst, one business/strategy analyst, both with some software development skills) who are looking for interesting side projects they can deliver together as a small team.

INCLUDE procurements where a small two-person team could realistically do the work, such as:
- Data analysis, statistical analysis, or data processing studies
- Surveys, user research, or needs assessments (vajaduste uuring)
- Market research or sector overviews (turu-uuring, ülevaade)
- Feasibility studies or impact assessments (teostatavusuuring, mõjuanalüüs)
- Strategy reports, policy analysis, or consulting briefs
- Evaluations, audits, or monitoring frameworks (hindamine, audit)
- Dashboard, reporting design, or data visualisation
- Any knowledge-work deliverable: a report, analysis, or recommendation
- Simpler software projects: websites, landing pages, small web apps, simple automation scripts, CMS setup, chatbots, form-based tools, API integrations
- Small digital tools or prototypes that don't require a large dev team or complex infrastructure

EXCLUDE procurements that require:
- Physical goods, equipment, construction, or civil engineering works (even if framed as a study or supervision role requiring on-site presence)
- Manual labour: cleaning, maintenance, landscaping, security guarding, waste management
- Large teams, agencies, or accredited institutions
- Specific professional licences (legal, medical, certified audit firms)
- Large-scale or highly complex software systems (ERP, national-scale infrastructure, real-time safety-critical systems)
- Staffing, recruitment, or managed services
- Transport, logistics, catering, or facility services

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
    url = BASE_URL.format(ref) if ref else "https://riigihanked.riik.ee/rhr-web/#/procurement-search"

    lines = [f"• {name}"]
    if cpv:
        lines.append(f"  Kategooria: {cpv}")
    if buyer:
        lines.append(f"  Hankija: {buyer}")
    if date:
        lines.append(f"  Avaldatud: {date}")
    lines.append(f"  Link: {url}")
    return "\n".join(lines)


def format_procurement_brief(p: dict) -> str:
    name = p.get("procurementName") or "Pealkiri puudub"
    ref = p.get("procurementReferenceNr") or ""
    buyer = p.get("contractingAuthorityName") or ""
    url = BASE_URL.format(ref) if ref else "https://riigihanked.riik.ee/rhr-web/#/procurement-search"
    buyer_part = f" ({buyer})" if buyer else ""
    return f"  – {name}{buyer_part}\n    {url}"


def send_email(relevant: list, all_procurements: list, lookback_hours: int, gmail_user: str, gmail_password: str, recipient: str):
    today = datetime.now().strftime("%d.%m.%Y")
    total = len(all_procurements)
    all_list = "\n".join(format_procurement_brief(p) for p in all_procurements)

    if not relevant:
        subject = f"[Riigihanked {today}] Sobivaid hankeid ei leitud"
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total}\n\n"
            f"Viimase {lookback_hours} tunni jooksul ei leitud potentsiaalselt sobivaid riigihankeid.\n\n"
            f"Kõik kontrollitud hanked:\n{all_list}"
        )
    else:
        subject = f"[Riigihanked {today}] {len(relevant)} potentsiaalselt sobivat hanget"
        items = "\n\n".join(format_procurement(p) for p in relevant)
        body = (
            f"Kuupäev: {today}\n"
            f"Kontrollitud hankeid: {total} | Sobivaid: {len(relevant)}\n\n"
            "Potentsiaalselt sobivad riigihanked:\n\n"
            f"{items}\n\n"
            f"---\nKõik kontrollitud hanked:\n{all_list}"
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
    lookback_hours = 168
    date_to = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_from = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print(f"Fetching procurements from {date_from} to {date_to}...")
    procurements = fetch_procurements(date_from, date_to)
    print(f"Total procurements: {len(procurements)}")

    gemini_api_key = os.environ["GROQ_API_KEY"]
    relevant = filter_with_groq(procurements, gemini_api_key)
    print(f"Relevant to the client: {len(relevant)}")

    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    send_email(relevant, procurements, lookback_hours, gmail_user, gmail_password, recipient)


if __name__ == "__main__":
    main()
