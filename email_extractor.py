"""Step 2 — Email Extractor: visit lead websites and extract contact emails."""

import re
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from robotexclusionrulesparser import RobotExclusionRulesParser

from models import get_db, update_lead, get_leads_needing_email
from logger import get_logger

log = get_logger("email_extractor")

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

SKIP_PREFIXES = {"info@", "noreply@", "no-reply@", "support@", "admin@",
                 "webmaster@", "sales@", "hello@", "contact@"}

CONTACT_PATHS = ["/", "/contact", "/contact-us", "/about", "/about-us"]

HEADERS = {
    "User-Agent": "SEOAuditBot/1.0 (+https://yourdomain.com/bot)",
    "Accept": "text/html",
}

REQUEST_TIMEOUT = 15


def _can_fetch(base_url: str, path: str) -> bool:
    """Check robots.txt for permission to scrape the path."""
    try:
        robots_url = urljoin(base_url, "/robots.txt")
        resp = requests.get(robots_url, timeout=10, headers=HEADERS)
        if resp.status_code != 200:
            return True  # No robots.txt = allowed
        rp = RobotExclusionRulesParser()
        rp.parse(resp.text)
        return rp.is_allowed("SEOAuditBot", path)
    except Exception:
        return True  # On error, assume allowed


def _extract_emails_from_html(html: str) -> set[str]:
    """Pull all email addresses from page HTML."""
    # Check raw HTML for emails
    found = set(EMAIL_RE.findall(html))

    # Also check mailto: links
    soup = BeautifulSoup(html, "lxml")
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip()
            if EMAIL_RE.match(email):
                found.add(email)

    return found


def _score_email(email: str) -> int:
    """Higher score = more likely to be a real person's email."""
    email_lower = email.lower()

    # Skip generic addresses
    for prefix in SKIP_PREFIXES:
        if email_lower.startswith(prefix):
            return 0

    # Prefer emails with name-like patterns
    local = email_lower.split("@")[0]
    if "." in local or "_" in local:
        return 3  # Likely firstname.lastname
    if any(c.isdigit() for c in local):
        return 1
    return 2


def _pick_best_email(emails: set[str]) -> str | None:
    """Pick the most owner-like email from the set."""
    if not emails:
        return None

    # Filter out image/file extensions that regex might catch
    filtered = {
        e for e in emails
        if not e.lower().endswith((".png", ".jpg", ".gif", ".svg", ".webp", ".css", ".js"))
    }

    if not filtered:
        return None

    scored = [(e, _score_email(e)) for e in filtered]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Only return if at least score > 0
    best_email, best_score = scored[0]
    return best_email if best_score > 0 else None


def extract_email_for_lead(lead: dict) -> str | None:
    """Visit a lead's website and attempt to extract an email address."""
    base_url = lead["website"]
    all_emails: set[str] = set()

    for path in CONTACT_PATHS:
        if not _can_fetch(base_url, path):
            log.debug(f"  robots.txt blocks {path} on {base_url}")
            continue

        url = urljoin(base_url, path)
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS,
                                allow_redirects=True)
            if resp.status_code == 200:
                page_emails = _extract_emails_from_html(resp.text)
                all_emails.update(page_emails)
        except requests.RequestException:
            continue

    return _pick_best_email(all_emails)


def run_email_extraction() -> dict:
    """Process all leads needing email extraction. Returns stats."""
    conn = get_db()
    leads = get_leads_needing_email(conn)
    log.info(f"Email extraction: {len(leads)} leads to process")

    stats = {"processed": 0, "found": 0, "skipped": 0}

    for lead in leads:
        stats["processed"] += 1
        try:
            email = extract_email_for_lead(dict(lead))
            if email:
                update_lead(conn, lead["id"], email=email, email_status="found")
                stats["found"] += 1
                log.info(f"  Found: {lead['business_name']} -> {email}")
            else:
                update_lead(conn, lead["id"], email_status="skip")
                stats["skipped"] += 1
                log.info(f"  No email: {lead['business_name']}")
        except Exception as e:
            log.error(f"  Error on {lead['business_name']}: {e}")
            update_lead(conn, lead["id"], email_status="error")
            stats["skipped"] += 1

    conn.close()
    log.info(f"Email extraction done: {stats}")
    return stats


if __name__ == "__main__":
    stats = run_email_extraction()
    print(f"Results: {stats}")
