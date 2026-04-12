"""Step 1 — Google Maps Scraper (dual-mode: Playwright + SerpAPI fallback).

Modes (set via SCRAPER_MODE in .env):
  "auto"       — Try Playwright first; if it fails, fall back to SerpAPI.
  "playwright" — Playwright only (free, no API key needed).
  "serpapi"    — SerpAPI only (paid, most reliable).
"""

import random
import re
import time

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

from models import get_db, insert_lead
from config import Config
from logger import get_logger

log = get_logger("scraper")

SERPAPI_URL = "https://serpapi.com/search.json"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _delay():
    """Sleep a randomised interval between SCRAPE_DELAY_MIN and SCRAPE_DELAY_MAX."""
    secs = random.uniform(Config.SCRAPE_DELAY_MIN, Config.SCRAPE_DELAY_MAX)
    time.sleep(secs)


def _normalize_website(url: str | None) -> str | None:
    """Ensure URL has a scheme. Return None for empty/invalid."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _store_leads(conn, leads: list[dict], city: str, category: str) -> int:
    """Insert a batch of lead dicts into the database. Returns count inserted."""
    inserted = 0
    for biz in leads:
        website = _normalize_website(biz.get("website"))
        if not website:
            continue

        lead = {
            "business_name": biz.get("name") or biz.get("business_name", "Unknown"),
            "website": website,
            "phone": biz.get("phone"),
            "maps_url": biz.get("maps_url", ""),
            "city": city,
            "category": category,
        }

        row_id = insert_lead(conn, lead)
        if row_id:
            inserted += 1
            log.info(f"  + {lead['business_name']} ({website})")

    return inserted


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backend 1 — Playwright (free, headless Chrome)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _scrape_playwright(city: str, category: str,
                       max_results: int = 100) -> list[dict]:
    """Scrape Google Maps with headless Chromium via Playwright."""
    results: list[dict] = []
    query = f"{category} in {city}"
    search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

    log.info(f"[playwright] Navigating to Google Maps: {query}")
    if Config.PROXY_URL:
        log.info(f"[playwright] Routing through proxy: {Config.PROXY_URL.split('@')[-1]}")

    with sync_playwright() as p:
        # ── Launch browser (with optional proxy) ──
        launch_opts: dict = {"headless": True}
        if Config.PROXY_URL:
            launch_opts["proxy"] = {"server": Config.PROXY_URL}

        browser = p.chromium.launch(**launch_opts)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.error(f"[playwright] Failed to load Google Maps: {e}")
            browser.close()
            return results

        # Dismiss cookie consent if shown
        try:
            consent = page.locator("button:has-text('Accept all')")
            if consent.count() > 0:
                consent.first.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        # Wait for the results feed
        try:
            page.wait_for_selector("[role='feed']", timeout=15000)
        except PwTimeout:
            try:
                page.wait_for_selector("div.Nv2PK", timeout=10000)
            except PwTimeout:
                log.error("[playwright] Results feed did not load — possible block or CAPTCHA")
                browser.close()
                return results

        # ── Scroll to load cards ──
        feed = page.locator("[role='feed']").first
        stall_count = 0
        last_count = 0

        while stall_count < 6:
            cards = page.locator("div.Nv2PK").all()
            if len(cards) >= max_results:
                break
            if len(cards) == last_count:
                stall_count += 1
            else:
                stall_count = 0
            last_count = len(cards)

            try:
                feed.evaluate("el => el.scrollTop += 900")
            except Exception:
                page.mouse.wheel(0, 700)
            page.wait_for_timeout(1300)

        cards = page.locator("div.Nv2PK").all()
        total_cards = min(len(cards), max_results)
        log.info(f"[playwright] Found {len(cards)} cards, processing {total_cards}")

        # ── Click into each card and extract details ──
        for i, card in enumerate(cards[:total_cards]):
            biz: dict = {"name": None, "website": None, "phone": None}

            try:
                name_el = card.locator("a.hfpxzc")
                if name_el.count() == 0:
                    continue

                biz["name"] = name_el.first.get_attribute("aria-label")
                name_el.first.click()

                # Polite delay before reading the detail pane
                _delay()

                # Phone
                phone_btn = page.locator(
                    "button[data-tooltip='Copy phone number'], "
                    "[data-item-id*='phone']"
                )
                if phone_btn.count() > 0:
                    raw = phone_btn.first.get_attribute("aria-label") or ""
                    m = re.search(r"[\(\d][\d\s\(\)\-\.]{8,}", raw)
                    if m:
                        biz["phone"] = m.group().strip()

                # Website
                web_btn = page.locator(
                    "a[data-tooltip='Open website'], "
                    "[data-item-id='authority']"
                )
                if web_btn.count() > 0:
                    biz["website"] = web_btn.first.get_attribute("href")

                # Navigate back to results list
                back = page.locator("button[aria-label='Back']")
                if back.count() > 0:
                    back.first.click()
                    page.wait_for_timeout(1000)

            except Exception as e:
                log.warning(f"[playwright] Error on card {i}: {e}")

            if biz["name"]:
                results.append(biz)

        browser.close()

    log.info(f"[playwright] Extracted {len(results)} businesses")
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backend 2 — SerpAPI (paid, reliable)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _scrape_serpapi(city: str, category: str,
                    max_results: int = 100) -> list[dict]:
    """Scrape Google Maps via SerpAPI."""
    if not Config.SERPAPI_KEY:
        log.error("[serpapi] SERPAPI_KEY not set in .env — cannot use SerpAPI backend")
        return []

    results: list[dict] = []
    start = 0
    query = f"{category} in {city}"

    log.info(f"[serpapi] Searching: {query}")

    while len(results) < max_results:
        params = {
            "engine": "google_maps",
            "q": query,
            "type": "search",
            "api_key": Config.SERPAPI_KEY,
            "start": start,
            "hl": "en",
        }

        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            log.error(f"[serpapi] Request failed: {e}")
            break

        local = data.get("local_results", [])
        if not local:
            log.info("[serpapi] No more results")
            break

        for biz in local:
            results.append({
                "name": biz.get("title", "Unknown"),
                "website": biz.get("website"),
                "phone": biz.get("phone"),
                "maps_url": biz.get("place_id_search",
                                    biz.get("data_id", "")),
            })
            if len(results) >= max_results:
                break

        start += len(local)
        if "serpapi_pagination" not in data:
            break

        _delay()  # Be polite to SerpAPI too

    log.info(f"[serpapi] Retrieved {len(results)} businesses")
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API — dual-mode dispatcher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def scrape_google_maps(city: str, category: str,
                       max_results: int = 100,
                       mode: str | None = None) -> int:
    """
    Scrape Google Maps for businesses, store in SQLite.

    Args:
        city:        Target city (e.g. "Dallas TX").
        category:    Business type (e.g. "dentist").
        max_results: How many leads to aim for.
        mode:        Override for SCRAPER_MODE ("playwright", "serpapi", "auto").
                     Defaults to Config.SCRAPER_MODE.

    Returns:
        Count of new leads inserted into the database.
    """
    mode = (mode or Config.SCRAPER_MODE).lower().strip()
    log.info(f"Scraper mode: {mode}")

    businesses: list[dict] = []

    if mode == "playwright":
        businesses = _scrape_playwright(city, category, max_results)

    elif mode == "serpapi":
        businesses = _scrape_serpapi(city, category, max_results)

    elif mode == "auto":
        # Try Playwright first
        log.info("Auto mode: attempting Playwright scraper first")
        try:
            businesses = _scrape_playwright(city, category, max_results)
        except Exception as e:
            log.warning(f"Playwright scraper failed: {e}")

        if len(businesses) < 5:
            log.warning(
                f"Playwright returned only {len(businesses)} results — "
                "falling back to SerpAPI"
            )
            businesses = _scrape_serpapi(city, category, max_results)
            if businesses:
                log.info(f"SerpAPI fallback succeeded with {len(businesses)} results")
        else:
            log.info(f"Playwright succeeded with {len(businesses)} results — SerpAPI not needed")

    else:
        log.error(f"Unknown SCRAPER_MODE: '{mode}'. Use 'playwright', 'serpapi', or 'auto'.")
        return 0

    if not businesses:
        log.warning(f"No businesses found for '{category}' in '{city}'")
        return 0

    # ── Store in database ──
    conn = get_db()
    inserted = _store_leads(conn, businesses, city, category)
    conn.close()

    log.info(f"Scraping complete: {inserted} new leads inserted "
             f"({len(businesses)} scraped, duplicates skipped)")
    return inserted


if __name__ == "__main__":
    import sys

    city = sys.argv[1] if len(sys.argv) > 1 else "Dallas TX"
    category = sys.argv[2] if len(sys.argv) > 2 else "dentist"
    mode = sys.argv[3] if len(sys.argv) > 3 else None

    from models import init_db
    init_db()

    count = scrape_google_maps(city, category, mode=mode)
    print(f"Done. {count} leads added.")
