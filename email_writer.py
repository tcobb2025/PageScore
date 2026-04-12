"""Step 4 — Claude Email Writer: generate personalized cold emails."""

import json
import anthropic

from models import get_db, update_lead, get_flagged_leads_needing_email_copy
from config import Config
from logger import get_logger

log = get_logger("email_writer")


def _find_worst_finding(findings: dict) -> str:
    """Identify the worst SEO issue to use as a teaser."""
    issues = []

    if not findings.get("is_https", True):
        issues.append(("Your website is not using HTTPS, which means visitors "
                       "see a 'Not Secure' warning in their browser", 30))

    status = findings.get("status_code")
    if status and status != 200:
        issues.append((f"Your homepage is returning a {status} error, meaning "
                       "some visitors can't even load your site", 25))

    ps = findings.get("pagespeed_mobile")
    if ps is not None and ps < 50:
        issues.append((f"Your website loads very slowly on mobile devices "
                       f"(Google speed score: {ps}/100), which causes visitors "
                       "to leave before the page finishes loading", 20))

    meta = findings.get("meta_description")
    if meta == "missing":
        issues.append(("Your website is missing a meta description, which means "
                       "Google is guessing what to show in search results", 15))

    h1 = findings.get("h1_tag")
    if h1 == "missing":
        issues.append(("Your homepage is missing an H1 heading tag, which "
                       "hurts your Google ranking", 15))

    missing_alt = findings.get("images_missing_alt", 0)
    checked = findings.get("images_checked", 0)
    if checked > 0 and missing_alt > 0:
        issues.append((f"{missing_alt} of {checked} images on your homepage "
                       "are missing alt text, which hurts your Google image "
                       "search visibility", 10))

    if not issues:
        return "We found several areas where your website could improve"

    issues.sort(key=lambda x: x[1], reverse=True)
    return issues[0][0]


def generate_cold_email(lead: dict) -> str:
    """Use Claude to write a personalized cold email for a flagged lead."""
    findings = json.loads(lead["seo_findings"]) if lead["seo_findings"] else {}
    worst_issue = _find_worst_finding(findings)

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    prompt = f"""Write a cold email to a local business about their website SEO issues.

Business name: {lead['business_name']}
Business category: {lead['category']}
City: {lead['city']}
SEO score: {lead['seo_score']}/100
Worst finding (use this as the free teaser): {worst_issue}

Rules:
- Address them by business name
- Mention their specific score
- Reference the worst finding as a free teaser
- Say we found additional issues and the full report is available for $49
- Include the text {{{{STRIPE_LINK}}}} exactly where the payment link should go
- Maximum 4 sentences in the body
- Friendly, direct tone — no buzzwords, no "leverage", no "unlock"
- End with a one-line unsubscribe footer: "Reply STOP to unsubscribe."
- Subject line on its own line at the top prefixed with "Subject: "
- Do not include any greeting like "Hi" or "Dear" — start with their name directly
- Do not include a sign-off name — just end with the unsubscribe line

Return ONLY the email text, nothing else."""

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

            # Inject real Stripe link
            if Config.STRIPE_PAYMENT_LINK:
                email_text = email_text.replace(
                    "{{STRIPE_LINK}}", Config.STRIPE_PAYMENT_LINK
                )

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
