"""Step 4 — Cold email writer: deterministic template, no LLM required."""

import json
import os
import random
from urllib.parse import urlencode, urlparse

from models import get_db, update_lead, get_flagged_leads_needing_email_copy
from config import Config
from logger import get_logger

log = get_logger("email_writer")

REPORT_BASE = os.getenv("REPORT_PAGE_BASE_URL", "https://pagescore-hq.com/report")

# Subject-line A/B variants. Instantly can A/B-test subject lines natively;
# we generate one email per lead with a pre-picked variant and log which
# variant was chosen so we can track conversion by variant.
SUBJECT_VARIANTS = {
    "A": "Quick question about {domain}",
    "B": "Found something on {domain}",
}
_subject_rng = random.SystemRandom()


def pick_subject_variant() -> str:
    """Return 'A' or 'B' with a 50/50 split (crypto-grade RNG)."""
    return _subject_rng.choice(["A", "B"])

# Map raw category strings (as scraped) to plain-English labels.
CATEGORY_LABELS = {
    "hvac": "HVAC",
    "roofer": "roofing",
    "roofing": "roofing",
    "roofing contractor": "roofing",
    "plumber": "plumbing",
    "plumbing": "plumbing",
    "electrician": "electrical",
    "electrical": "electrical",
    "dentist": "dental",
    "dental": "dental",
    "chiropractor": "chiropractic",
    "chiropractic": "chiropractic",
}

# Conservative "typically worth $low-$high in additional jobs per month" ranges.
CATEGORY_VALUE = {
    "roofing": (2000, 4000),
    "HVAC": (800, 1500),
    "plumbing": (600, 1000),
    "electrical": (600, 900),
    "dental": (1500, 3000),
    "chiropractic": (400, 800),
}
DEFAULT_VALUE = (500, 1000)


def plain_category(raw: str | None) -> str:
    """Return the plain-English category label for emails and the landing page."""
    if not raw:
        return "Local"
    key = raw.strip().lower()
    if key in CATEGORY_LABELS:
        return CATEGORY_LABELS[key]
    # Default: capitalize first letter of the raw category.
    return raw.strip()[:1].upper() + raw.strip()[1:]


def _value_range(category_label: str) -> tuple[int, int]:
    return CATEGORY_VALUE.get(category_label, DEFAULT_VALUE)


def _format_money(n: int) -> str:
    return f"{n:,}"


def _domain_from_website(website: str) -> str:
    """Strip protocol/www from a website URL — 'https://www.dalcoac.com/' -> 'dalcoac.com'."""
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


def _count_issues(findings: dict) -> int:
    """Rough issue count for the issues= URL param."""
    count = 0
    if not findings.get("is_https", True):
        count += 1
    if findings.get("status_code") and findings["status_code"] != 200:
        count += 1
    ps = findings.get("pagespeed_mobile")
    if ps is not None and ps < 50:
        count += 1
    if findings.get("meta_description") in ("missing", "empty"):
        count += 1
    if findings.get("h1_tag") in ("missing", "multiple"):
        count += 1
    if findings.get("images_missing_alt", 0) > 0:
        count += 1
    return max(count, 1)


def build_report_url(business_name: str, score: int, issues_count: int, email: str) -> str:
    """Build the personalized report landing page URL with properly encoded params."""
    params = urlencode({
        "company": business_name or "",
        "score": score if score is not None else 0,
        "issues": issues_count,
        "email": email or "",
    })
    return f"{REPORT_BASE}?{params}"


def render_cold_email(*, business_name: str, domain: str, score: int, city: str,
                      category_raw: str, report_url: str,
                      subject_variant: str = "A") -> str:
    """Render the final deterministic cold-email template for the given variant."""
    cat = plain_category(category_raw)
    low, high = _value_range(cat)
    city_clean = (city or "your area").strip()

    subject_template = SUBJECT_VARIANTS.get(subject_variant, SUBJECT_VARIANTS["A"])
    subject = f"Subject: {subject_template.format(domain=domain)}"
    greeting = f"Hi {business_name},"
    body = (
        f"Ran a quick audit on {domain} and your site scored {score}/100. "
        f"Sites in this range rarely show up when someone in {city_clean} "
        f'googles "{cat} near me" — and that\'s where most new customers come from. '
        f"For {cat} companies, showing up in those results is typically worth "
        f"${_format_money(low)}-${_format_money(high)} in additional jobs per month."
    )
    cta = f"See exactly what's costing you customers:\n{report_url}"
    signoff = "Alex, PageScore HQ"
    unsub = 'To opt out reply with "unsubscribe"'
    privacy = "Privacy policy: pagescore-hq.com/privacy.html"

    return "\n".join([
        subject,
        "",
        greeting,
        "",
        body,
        "",
        cta,
        "",
        signoff,
        "",
        unsub,
        privacy,
    ])


def generate_cold_email(lead: dict, subject_variant: str | None = None) -> tuple[str, str]:
    """Produce the cold email for a flagged lead.

    Returns (email_text, variant_letter). If no variant is passed, one is
    picked at random so callers that just want the text still get a valid
    A/B assignment.
    """
    findings = json.loads(lead["seo_findings"]) if lead["seo_findings"] else {}
    domain = _domain_from_website(lead.get("website", ""))
    issues_count = _count_issues(findings)
    report_url = build_report_url(
        lead["business_name"], lead["seo_score"] or 0, issues_count, lead.get("email", "")
    )
    variant = subject_variant if subject_variant in SUBJECT_VARIANTS else pick_subject_variant()
    email_text = render_cold_email(
        business_name=lead["business_name"],
        domain=domain,
        score=lead["seo_score"] or 0,
        city=lead.get("city", ""),
        category_raw=lead.get("category", ""),
        report_url=report_url,
        subject_variant=variant,
    )
    return email_text, variant


def run_email_generation() -> dict:
    """Generate cold emails for all flagged leads that need one."""
    conn = get_db()
    leads = get_flagged_leads_needing_email_copy(conn)
    log.info(f"Email generation: {len(leads)} leads to process")

    stats = {"processed": 0, "generated": 0, "errors": 0, "variant_A": 0, "variant_B": 0}

    for lead in leads:
        stats["processed"] += 1
        try:
            email_text, variant = generate_cold_email(dict(lead))
            update_lead(conn, lead["id"], cold_email=email_text, subject_variant=variant)
            stats["generated"] += 1
            stats[f"variant_{variant}"] += 1
            log.info(f"  Generated email for: {lead['business_name']} (variant {variant})")
        except Exception as e:
            stats["errors"] += 1
            log.error(f"  Error generating email for {lead['business_name']}: {e}")

    conn.close()
    log.info(f"Email generation done: {stats}")
    return stats


if __name__ == "__main__":
    stats = run_email_generation()
    print(f"Results: {stats}")
