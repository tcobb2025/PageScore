"""Step 4 — Claude Email Writer: generate personalized cold emails."""

import json
import os
from urllib.parse import urlencode, urlparse
import anthropic

from models import get_db, update_lead, get_flagged_leads_needing_email_copy
from config import Config
from logger import get_logger

log = get_logger("email_writer")

REPORT_BASE = os.getenv("REPORT_PAGE_BASE_URL", "https://pagescore-hq.com/report")


def _find_worst_finding(findings: dict) -> str:
    """Identify the worst SEO issue to use as a teaser."""
    issues = []

    if not findings.get("is_https", True):
        issues.append(("the biggest issue is that visitors see a 'Not Secure' "
                       "warning in Chrome before they even see your services", 30))

    status = findings.get("status_code")
    if status and status != 200:
        issues.append((f"your homepage returns a {status} error, so some "
                       "visitors can't load your site at all", 28))

    ps = findings.get("pagespeed_mobile")
    if ps is not None and ps < 50:
        issues.append((f"your mobile speed score is {ps}/100, which is slow "
                       "enough that visitors leave before the page loads", 22))

    meta = findings.get("meta_description")
    if meta in ("missing", "empty"):
        issues.append(("your meta description is missing, so Google is "
                       "guessing what to show beneath your search listing", 15))

    h1 = findings.get("h1_tag")
    if h1 == "missing":
        issues.append(("your homepage has no H1 heading, which weakens how "
                       "Google reads your main topic", 14))

    missing_alt = findings.get("images_missing_alt", 0)
    checked = findings.get("images_checked", 0)
    if checked and missing_alt:
        issues.append((f"{missing_alt} of {checked} images are missing alt "
                       "text, costing you Google Image Search traffic", 8))

    if not issues:
        return "we found several areas where your website could rank better"

    issues.sort(key=lambda x: x[1], reverse=True)
    return issues[0][0]


def _count_issues(findings: dict) -> int:
    """Rough count of findings for the issues param in the URL."""
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


def _domain_from_website(website: str) -> str:
    """Extract a clean domain like 'dalcoac.com' from a website URL."""
    if not website:
        return ""
    try:
        parsed = urlparse(website if "://" in website else f"http://{website}")
        host = parsed.netloc or parsed.path
        return host.replace("www.", "").strip("/").lower()
    except Exception:
        return website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]


def build_report_url(business_name: str, score: int, issues_count: int, email: str) -> str:
    """Build the personalized report landing page URL with encoded params."""
    params = urlencode({
        "company": business_name or "",
        "score": score if score is not None else 0,
        "issues": issues_count,
        "email": email or "",
    })
    return f"{REPORT_BASE}?{params}"


def generate_cold_email(lead: dict) -> str:
    """Use Claude to write a personalized cold email for a flagged lead."""
    findings = json.loads(lead["seo_findings"]) if lead["seo_findings"] else {}
    worst_issue = _find_worst_finding(findings)
    domain = _domain_from_website(lead.get("website", ""))
    issues_count = _count_issues(findings)
    report_url = build_report_url(
        lead["business_name"], lead["seo_score"] or 0, issues_count, lead.get("email", "")
    )

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    prompt = f"""Write a short cold email to a local business owner about their website SEO.

Business name: {lead['business_name']}
Domain: {domain}
SEO score: {lead['seo_score']}/100
Single worst finding (use this specifically): {worst_issue}
Report URL: {report_url}

Strict format — output ONLY these parts in this exact order, nothing else:

Subject: Quick question about {domain}

Hi {lead['business_name']},

<body: 3 to 5 sentences, warm, direct, human. Mention that you ran a quick audit on {domain} and their site scored {lead['seo_score']}/100. Then work in the worst finding naturally. Then say you found a few other things hurting their ranking too. Do not include the report URL in the body.>

See your full score here:
{report_url}

Alex, PageScore HQ

To opt out reply with "unsubscribe"

Rules:
- No buzzwords (leverage, unlock, synergy, solutions, cutting-edge)
- No exclamation points
- No "I hope this finds you well" or similar filler
- No sign-off other than "Alex, PageScore HQ"
- Do not add any text before "Subject:" or after the unsubscribe line
- Keep the body to 5 sentences maximum"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


def run_email_generation() -> dict:
    """Generate cold emails for all flagged leads that need one."""
    conn = get_db()
    leads = get_flagged_leads_needing_email_copy(conn)
    log.info(f"Email generation: {len(leads)} leads to process")

    stats = {"processed": 0, "generated": 0, "errors": 0}

    for lead in leads:
        stats["processed"] += 1
        try:
            email_text = generate_cold_email(dict(lead))
            update_lead(conn, lead["id"], cold_email=email_text)
            stats["generated"] += 1
            log.info(f"  Generated email for: {lead['business_name']}")

        except Exception as e:
            stats["errors"] += 1
            log.error(f"  Error generating email for {lead['business_name']}: {e}")

    conn.close()
    log.info(f"Email generation done: {stats}")
    return stats


if __name__ == "__main__":
    stats = run_email_generation()
    print(f"Results: {stats}")
