"""Step 5 — Instantly Integration: add leads to campaign via Instantly v2 API."""

import re
import requests
from datetime import datetime
from urllib.parse import urlencode, urlparse

from models import get_db, update_lead, get_leads_ready_to_send, count_emails_sent_today
from config import Config
from logger import get_logger

log = get_logger("email_sender")

INSTANTLY_API_V2 = "https://api.instantly.ai/api/v2"

# Generic email prefixes that don't represent a person's name
GENERIC_PREFIXES = {
    "info", "contact", "hello", "support", "staff", "team",
    "admin", "office", "sales", "billing", "reception", "front",
    "desk", "mail", "webmaster", "noreply", "donotreply", "service",
    "marketing", "help", "general", "enquiries", "inquiries",
}

# Words to strip from company name when building a short name fallback
COMPANY_STRIP_WORDS = [
    " LLC", " Inc", " Co", " Corp", " Ltd", " LLP",
    " Construction", " Roofing", " HVAC", " Plumbing",
    " Electric", " Electrical", " Services", " Solutions",
    " Contractors", " Contracting", " General",
]


def extract_first_name(email: str, company_name: str) -> str:
    """Extract a first name from email address, falling back to shortened company name."""
    if email:
        username = email.split("@")[0].lower()
        # Split on common separators to get first part
        parts = re.split(r'[._\-]', username)
        first = parts[0] if parts else username

        # Valid if: single alpha word, no numbers, not generic, at least 2 chars
        if (first.isalpha() and
                len(first) >= 2 and
                first not in GENERIC_PREFIXES and
                not any(c.isdigit() for c in first)):
            return first.capitalize()

    # Fallback: shorten company name
    if not company_name:
        return "there"

    name = company_name.strip()

    # Cut at first & or comma
    for sep in ["&", ","]:
        if sep in name:
            name = name[:name.index(sep)].strip()
            break

    # Strip trailing business/industry words
    for word in COMPANY_STRIP_WORDS:
        if name.lower().endswith(word.lower()):
            name = name[:len(name) - len(word)].strip()

    return name if name else company_name.strip()


def strip_state_from_city(city: str) -> str:
    """Strip 2-letter state abbreviation: 'Dallas TX' -> 'Dallas'."""
    if not city:
        return ""
    city = city.strip()
    parts = city.rsplit(" ", 1)
    if len(parts) > 1 and len(parts[-1]) == 2 and parts[-1].isupper():
        return parts[0]
    return city


def _domain_from_website(website: str) -> str:
    """Strip protocol/www from a website URL."""
    if not website:
        return ""
    try:
        parsed = urlparse(website if "://" in website else f"http://{website}")
        host = parsed.netloc or parsed.path
        return host.replace("www.", "").strip("/").lower()
    except Exception:
        return (website.replace("https://", "")
                       .replace("http://", "")
                       .replace("www.", "")
                       .split("/")[0]
                       .lower())


def add_lead_to_campaign(lead: dict, first_name: str, subject: str,
                         report_url: str, city_clean: str,
                         category_label: str, revenue_range: str) -> dict | None:
    """Add a single lead to the Instantly campaign via v2 API.

    Returns the API response dict on success, None on failure.
    """
    if not Config.INSTANTLY_API_KEY:
        log.error("INSTANTLY_API_KEY not set")
        return None
    if not Config.INSTANTLY_CAMPAIGN_ID:
        log.error("INSTANTLY_CAMPAIGN_ID not set")
        return None

    domain = _domain_from_website(lead.get("website", ""))
    score = str(lead.get("seo_score") or 0)

    payload = {
        "campaign": Config.INSTANTLY_CAMPAIGN_ID,
        "email": lead["email"],
        "first_name": first_name,
        "company_name": lead.get("business_name", ""),
        "custom_variables": {
            "subject": subject,
            "domain": domain,
            "score": score,
            "city": city_clean,
            "category": category_label,
            "revenue_range": revenue_range,
            "report_url": report_url,
        }
    }

    headers = {
        "Authorization": f"Bearer {Config.INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{INSTANTLY_API_V2}/leads",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"  Added lead to Instantly campaign: {lead['email']}")
        return data
    except requests.RequestException as e:
        error_detail = ""
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.text
            except Exception:
                pass
        log.error(f"  Failed to add lead: {lead['email']} — {e} {error_detail}")
        return None


def run_email_sending() -> dict:
    """Add leads to Instantly campaign respecting daily limits."""
    from email_writer import plain_category, _value_range, _format_money, \
        SUBJECT_VARIANTS, _domain_from_website as ew_domain, build_report_url, \
        _count_issues, pick_subject_variant

    import json

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
        lead = dict(lead)

        # Extract first name
        first_name = extract_first_name(lead.get("email", ""), lead.get("business_name", ""))

        # Build variables
        city_clean = strip_state_from_city(lead.get("city", ""))
        category_label = plain_category(lead.get("category", ""))
        low, high = _value_range(category_label)
        revenue_range = f"${_format_money(low)}-${_format_money(high)}"
        domain = _domain_from_website(lead.get("website", ""))

        # Subject variant
        variant = lead.get("subject_variant") or pick_subject_variant()
        subject_template = SUBJECT_VARIANTS.get(variant, SUBJECT_VARIANTS["A"])
        subject = subject_template.format(domain=domain)

        # Report URL
        findings = json.loads(lead["seo_findings"]) if lead.get("seo_findings") else {}
        issues_count = _count_issues(findings)
        report_url = build_report_url(
            lead["business_name"], lead.get("seo_score") or 0,
            issues_count, lead.get("email", "")
        )

        # Add to Instantly campaign
        result = add_lead_to_campaign(
            lead, first_name, subject, report_url,
            city_clean, category_label, revenue_range
        )

        if result is not None:
            stats["sent"] += 1
            # Extract lead ID if returned
            instantly_id = result.get("id") or result.get("lead_id") or ""
            update_lead(conn, lead["id"],
                        email_sent=1,
                        email_sent_at=datetime.now().isoformat(),
                        first_name=first_name,
                        instantly_lead_id=str(instantly_id),
                        added_to_campaign=1,
                        campaign_added_at=datetime.now().isoformat())
        else:
            stats["failed"] += 1
            # Still save the first_name even on failure
            update_lead(conn, lead["id"], first_name=first_name)

    conn.close()
    log.info(f"Email sending done: {stats}")
    return stats


def send_test_lead(email: str, first_name: str, domain: str, score: int,
                   city: str, category: str, revenue_range: str,
                   report_url: str, subject: str) -> dict | None:
    """Send a single test lead to the Instantly campaign. Returns full API response."""
    if not Config.INSTANTLY_API_KEY:
        log.error("INSTANTLY_API_KEY not set")
        return None
    if not Config.INSTANTLY_CAMPAIGN_ID:
        log.error("INSTANTLY_CAMPAIGN_ID not set")
        return None

    payload = {
        "campaign": Config.INSTANTLY_CAMPAIGN_ID,
        "email": email,
        "first_name": first_name,
        "custom_variables": {
            "subject": subject,
            "domain": domain,
            "score": str(score),
            "city": city,
            "category": category,
            "revenue_range": revenue_range,
            "report_url": report_url,
        }
    }

    headers = {
        "Authorization": f"Bearer {Config.INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        f"{INSTANTLY_API_V2}/leads",
        json=payload,
        headers=headers,
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text}")
    return resp.json() if resp.ok else {"error": resp.status_code, "body": resp.text}


if __name__ == "__main__":
    # Quick test: add tyler@cobb.org to the campaign
    result = send_test_lead(
        email="tyler@cobb.org",
        first_name="Tyler",
        domain="testroofing.com",
        score=38,
        city="Dallas",
        category="roofing",
        revenue_range="$2,000-$4,000",
        report_url="https://pagescore-hq.com/report?company=Test&score=38&issues=4&email=tyler%40cobb.org",
        subject="Quick question about testroofing.com",
    )
    print(f"\nResult: {result}")
