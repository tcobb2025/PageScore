"""Step 3 — SEO Audit Bot: lightweight audit of each lead's website."""

import json
import requests
from bs4 import BeautifulSoup

from models import get_db, update_lead, get_leads_needing_audit
from config import Config
from logger import get_logger

log = get_logger("seo_audit")

HEADERS = {
    "User-Agent": "SEOAuditBot/1.0 (+https://yourdomain.com/bot)",
}
REQUEST_TIMEOUT = 20
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
SCORE_THRESHOLD = 60


def _check_pagespeed(url: str) -> dict:
    """Get Google PageSpeed mobile score."""
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if Config.PAGESPEED_API_KEY:
        params["key"] = Config.PAGESPEED_API_KEY

    try:
        resp = requests.get(PAGESPEED_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        score_raw = (
            data.get("lighthouseResult", {})
            .get("categories", {})
            .get("performance", {})
            .get("score")
        )
        score = int(score_raw * 100) if score_raw is not None else None
        return {"pagespeed_mobile": score}
    except Exception as e:
        log.warning(f"  PageSpeed failed for {url}: {e}")
        return {"pagespeed_mobile": None}


def _fetch_page(url: str) -> tuple[int | None, BeautifulSoup | None]:
    """Fetch homepage and return (status_code, soup)."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS,
                            allow_redirects=True)
        return resp.status_code, BeautifulSoup(resp.text, "lxml")
    except requests.RequestException as e:
        log.warning(f"  Failed to fetch {url}: {e}")
        return None, None


def _check_https(url: str) -> bool:
    return url.lower().startswith("https://")


def _check_meta_description(soup: BeautifulSoup) -> dict:
    """Check for missing or duplicate meta descriptions."""
    meta_tags = soup.find_all("meta", attrs={"name": "description"})
    if not meta_tags:
        return {"meta_description": "missing"}
    if len(meta_tags) > 1:
        return {"meta_description": "duplicate"}
    content = meta_tags[0].get("content", "").strip()
    if not content:
        return {"meta_description": "empty"}
    return {"meta_description": "ok"}


def _check_h1(soup: BeautifulSoup) -> dict:
    h1_tags = soup.find_all("h1")
    if not h1_tags:
        return {"h1_tag": "missing"}
    if len(h1_tags) > 1:
        return {"h1_tag": "multiple"}
    return {"h1_tag": "ok"}


def _check_alt_text(soup: BeautifulSoup) -> dict:
    """Check first 10 images for alt text."""
    images = soup.find_all("img")[:10]
    if not images:
        return {"images_missing_alt": 0, "images_checked": 0}

    missing = sum(1 for img in images if not img.get("alt", "").strip())
    return {"images_missing_alt": missing, "images_checked": len(images)}


def _calculate_score(findings: dict) -> int:
    """Score 0-100 based on findings."""
    score = 100
    deductions = {
        "pagespeed": 0,
        "meta": 0,
        "h1": 0,
        "alt": 0,
        "https": 0,
        "broken": 0,
    }

    # PageSpeed: up to 30 points
    ps = findings.get("pagespeed_mobile")
    if ps is None:
        deductions["pagespeed"] = 15  # Can't check = partial deduction
    elif ps < 50:
        deductions["pagespeed"] = 30
    elif ps < 70:
        deductions["pagespeed"] = 20
    elif ps < 90:
        deductions["pagespeed"] = 10

    # Meta description: 15 points
    meta = findings.get("meta_description", "ok")
    if meta == "missing":
        deductions["meta"] = 15
    elif meta in ("duplicate", "empty"):
        deductions["meta"] = 10

    # H1: 15 points
    h1 = findings.get("h1_tag", "ok")
    if h1 == "missing":
        deductions["h1"] = 15
    elif h1 == "multiple":
        deductions["h1"] = 5

    # Alt text: up to 15 points
    checked = findings.get("images_checked", 0)
    missing_alt = findings.get("images_missing_alt", 0)
    if checked > 0:
        ratio = missing_alt / checked
        if ratio > 0.5:
            deductions["alt"] = 15
        elif ratio > 0:
            deductions["alt"] = 8

    # HTTPS: 15 points
    if not findings.get("is_https", True):
        deductions["https"] = 15

    # Broken homepage: 10 points
    status = findings.get("status_code")
    if status is not None and status != 200:
        deductions["broken"] = 10

    total_deductions = sum(deductions.values())
    return max(0, score - total_deductions)


def audit_website(url: str) -> tuple[int, dict]:
    """Run full audit on a URL. Returns (score, findings_dict)."""
    findings = {}

    # HTTPS check
    findings["is_https"] = _check_https(url)

    # Fetch page
    status_code, soup = _fetch_page(url)
    findings["status_code"] = status_code

    if soup:
        findings.update(_check_meta_description(soup))
        findings.update(_check_h1(soup))
        findings.update(_check_alt_text(soup))

    # PageSpeed (slow, run last)
    ps_result = _check_pagespeed(url)
    findings.update(ps_result)

    score = _calculate_score(findings)
    return score, findings


def run_seo_audit() -> dict:
    """Audit all leads that have an email but no score yet."""
    conn = get_db()
    leads = get_leads_needing_audit(conn)
    log.info(f"SEO audit: {len(leads)} leads to process")

    stats = {"processed": 0, "flagged": 0, "passed": 0, "errors": 0}

    for lead in leads:
        stats["processed"] += 1
        try:
            score, findings = audit_website(lead["website"])
            flagged = 1 if score < SCORE_THRESHOLD else 0

            update_lead(
                conn,
                lead["id"],
                seo_score=score,
                seo_findings=json.dumps(findings),
                flagged=flagged,
            )

            if flagged:
                stats["flagged"] += 1
                log.info(f"  FLAGGED: {lead['business_name']} score={score}")
            else:
                stats["passed"] += 1
                log.info(f"  OK: {lead['business_name']} score={score}")

        except Exception as e:
            stats["errors"] += 1
            log.error(f"  Error auditing {lead['business_name']}: {e}")

    conn.close()
    log.info(f"SEO audit done: {stats}")
    return stats


if __name__ == "__main__":
    stats = run_seo_audit()
    print(f"Results: {stats}")
