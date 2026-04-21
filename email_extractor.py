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

# Strict format gate: local@domain.tld with a real TLD (≥2 alpha chars)
VALID_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+\-]*"
    r"@[A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*"
    r"\.[A-Za-z]{2,}$"
)

# Specific addresses we never want to contact (e.g. our own test address)
BLOCKED_EMAILS = {"tyler@cobb.org"}

# Junk keywords — any local-part containing these is rejected outright
JUNK_KEYWORDS = {
    "filler", "test", "placeholder", "example",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "webmaster",
}

# Template/builder/placeholder/tracking domains — always reject
JUNK_DOMAINS = {
    "domain.com", "mysite.com", "example.com", "example.org",
    "godaddy.com", "wix.com", "wixsite.com",
    "wixpress.com", "sentry.wixpress.com",
    "squarespace.com", "squarespace.mail",
    "weebly.com", "insitesoft.com", "wordpress.com",
}

# Generic usernames — reject whenever the local-part is exactly one of these
GENERIC_USERNAMES = {
    "user", "you", "filler", "example", "test", "placeholder",
    "info", "hello", "contact", "support",
    "admin", "webmaster", "staff", "team", "office", "sales",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mail", "email", "billing", "help", "service", "hq",
}

# The low-confidence pathway is disabled — info/contact/hello/support are now
# hard-rejected by GENERIC_USERNAMES. Kept as an empty set for backward-compat.
LOW_CONFIDENCE_PREFIXES: set[str] = set()

# Covered by GENERIC_USERNAMES; retained for backward-compat imports
ADMIN_PREFIXES = {"admin@", "webmaster@"}


def _looks_like_tracking_hash(local: str) -> bool:
    """True if the local-part looks like a random tracking hash.

    Heuristic: longer than 15 chars, no name separators, mixes letters and digits
    (catches e.g. Sentry's Wix ingest hashes).
    """
    if len(local) <= 15:
        return False
    if any(c in local for c in "._-+"):
        return False
    return any(c.isalpha() for c in local) and any(c.isdigit() for c in local)


def is_valid_business_email(email: str) -> bool:
    """Return True only if `email` is a plausible real business address."""
    if not email:
        return False
    e = email.strip().lower()
    if e in BLOCKED_EMAILS:
        return False
    if not VALID_EMAIL_RE.match(e):
        return False
    local, domain = e.rsplit("@", 1)
    if domain in JUNK_DOMAINS or domain.startswith("sentry."):
        return False
    if local in GENERIC_USERNAMES:
        return False
    if any(kw in local for kw in JUNK_KEYWORDS):
        return False
    if _looks_like_tracking_hash(local):
        return False
    return True

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


def _is_junk(email: str) -> bool:
    """Return True if the email fails the business-email validator."""
    return not is_valid_business_email(email)


def _is_admin(email: str) -> bool:
    """Return True if this is an admin/webmaster address.

    These are already rejected by _is_junk — kept for backward compatibility.
    """
    lower = email.lower()
    return any(lower.startswith(p) for p in ADMIN_PREFIXES)


def _is_low_confidence(email: str) -> bool:
    """Return True if this is a generic prefix (info@, contact@, hello@, support@)."""
    lower = email.lower()
    return any(lower.startswith(p) for p in LOW_CONFIDENCE_PREFIXES)


def _score_email(email: str) -> int:
    """Higher score = more likely to be a real person's email."""
    email_lower = email.lower()

    # Low-confidence generics get a low but non-zero score
    if _is_low_confidence(email_lower):
        return 1

    # Prefer emails with name-like patterns
    local = email_lower.split("@")[0]
    if "." in local or "_" in local:
        return 4  # Likely firstname.lastname
    if any(c.isdigit() for c in local):
        return 2
    return 3


def _pick_best_email(emails: set[str]) -> tuple[str | None, str | None]:
    """Pick the best email. Returns (email, confidence) where confidence is 'high' or 'low'."""
    if not emails:
        return None, None

    # Filter out image/file extensions that regex might catch
    filtered = {
        e for e in emails
        if not e.lower().endswith((".png", ".jpg", ".gif", ".svg", ".webp", ".css", ".js"))
    }

    if not filtered:
        return None, None

    # Remove junk emails
    clean = {e for e in filtered if not _is_junk(e)}
    if not clean:
        return None, None

    # Separate admin-only emails — reject unless they're the only option
    non_admin = {e for e in clean if not _is_admin(e)}
    pool = non_admin if non_admin else clean

    scored = [(e, _score_email(e)) for e in pool]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_email, best_score = scored[0]
    if best_score <= 0:
        return None, None

    confidence = "low" if _is_low_confidence(best_email) else "high"
    return best_email, confidence


def extract_email_for_lead(lead: dict) -> tuple[str | None, str | None]:
    """Visit a lead's website and extract an email. Returns (email, confidence)."""
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

    stats = {"processed": 0, "found": 0, "found_low": 0, "skipped": 0}

    for lead in leads:
        stats["processed"] += 1
        try:
            email, confidence = extract_email_for_lead(dict(lead))
            if email:
                update_lead(conn, lead["id"], email=email,
                            email_status="found", email_confidence=confidence)
                if confidence == "low":
                    stats["found_low"] += 1
                    log.info(f"  Found (low confidence): {lead['business_name']} -> {email}")
                else:
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
