"""Step 5 — Instantly Integration: send cold emails via Instantly.ai API."""

import requests
from datetime import datetime

from models import get_db, update_lead, get_leads_ready_to_send, count_emails_sent_today
from config import Config
from logger import get_logger

log = get_logger("email_sender")

INSTANTLY_API_URL = "https://api.instantly.ai/api/v1"


def _parse_subject_and_body(cold_email: str) -> tuple[str, str]:
    """Split cold email into subject and body."""
    lines = cold_email.strip().split("\n")
    subject = ""
    body_lines = []
    found_subject = False

    for line in lines:
        if line.lower().startswith("subject:") and not found_subject:
            subject = line.split(":", 1)[1].strip()
            found_subject = True
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    if not subject:
        subject = "Quick question about your website"

    return subject, body


def send_via_instantly(to_email: str, subject: str, body: str,
                       from_name: str = "SEO Audit Team") -> bool:
    """Send a single email via Instantly API."""
    if not Config.INSTANTLY_API_KEY:
        log.error("INSTANTLY_API_KEY not set")
        return False

    endpoint = f"{INSTANTLY_API_URL}/unibox/emails/send"
    payload = {
        "api_key": Config.INSTANTLY_API_KEY,
        "email_body": body,
        "subject": subject,
        "to_address": to_email,
        "from_name": from_name,
    }

    try:
        resp = requests.post(endpoint, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error(f"  Instantly API error for {to_email}: {e}")
        return False


def run_email_sending() -> dict:
    """Send cold emails respecting daily limits."""
    conn = get_db()

    sent_today = count_emails_sent_today(conn)
    remaining = max(0, Config.DAILY_EMAIL_LIMIT - sent_today)

    if remaining == 0:
        log.info("Daily email limit reached, skipping send step")
        conn.close()
        return {"processed": 0, "sent": 0, "failed": 0, "skipped_limit": True}

    leads = get_leads_ready_to_send(conn, limit=remaining)
    log.info(f"Email sending: {len(leads)} to send ({sent_today} sent today, "
             f"{remaining} remaining)")

    stats = {"processed": 0, "sent": 0, "failed": 0}

    for lead in leads:
        stats["processed"] += 1
        subject, body = _parse_subject_and_body(lead["cold_email"])

        success = send_via_instantly(lead["email"], subject, body)
        if success:
            update_lead(
                conn, lead["id"],
                email_sent=1,
                email_sent_at=datetime.utcnow().isoformat(),
            )
            stats["sent"] += 1
            log.info(f"  Sent to: {lead['email']} ({lead['business_name']})")
        else:
            stats["failed"] += 1

    conn.close()
    log.info(f"Email sending done: {stats}")
    return stats


if __name__ == "__main__":
    stats = run_email_sending()
    print(f"Results: {stats}")
