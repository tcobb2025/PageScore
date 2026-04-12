#!/usr/bin/env python3
"""Step 8 — Orchestrator: run the full SEO lead generation pipeline."""

import argparse
import sys
import time
from datetime import datetime

from models import init_db
from scraper import scrape_google_maps
from email_extractor import run_email_extraction
from seo_audit import run_seo_audit
from email_writer import run_email_generation
from email_sender import run_email_sending
from logger import get_logger

log = get_logger("orchestrator")


def run_pipeline(city: str, category: str, skip_scrape: bool = False,
                 scraper_mode: str | None = None):
    """Execute the full pipeline in order."""
    start_time = time.time()
    log.info("=" * 60)
    log.info(f"Pipeline started: {city} / {category}")
    log.info(f"Timestamp: {datetime.now().isoformat()}")
    log.info("=" * 60)

    # Initialize database
    init_db()

    results = {}

    # Step 1: Scrape Google Maps
    if not skip_scrape:
        log.info("\n--- Step 1: Google Maps Scraping ---")
        try:
            count = scrape_google_maps(city, category, mode=scraper_mode)
            results["scrape"] = {"leads_added": count}
        except Exception as e:
            log.error(f"Scraper failed: {e}")
            results["scrape"] = {"error": str(e)}
    else:
        log.info("\n--- Step 1: Skipped (--skip-scrape) ---")

    # Step 2: Extract emails
    log.info("\n--- Step 2: Email Extraction ---")
    try:
        results["email_extraction"] = run_email_extraction()
    except Exception as e:
        log.error(f"Email extraction failed: {e}")
        results["email_extraction"] = {"error": str(e)}

    # Step 3: SEO Audit
    log.info("\n--- Step 3: SEO Audit ---")
    try:
        results["seo_audit"] = run_seo_audit()
    except Exception as e:
        log.error(f"SEO audit failed: {e}")
        results["seo_audit"] = {"error": str(e)}

    # Step 4: Generate cold emails
    log.info("\n--- Step 4: Cold Email Generation ---")
    try:
        results["email_generation"] = run_email_generation()
    except Exception as e:
        log.error(f"Email generation failed: {e}")
        results["email_generation"] = {"error": str(e)}

    # Step 5: Send emails
    log.info("\n--- Step 5: Email Sending ---")
    try:
        results["email_sending"] = run_email_sending()
    except Exception as e:
        log.error(f"Email sending failed: {e}")
        results["email_sending"] = {"error": str(e)}

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info(f"Pipeline complete in {elapsed:.1f}s")
    log.info(f"Results: {results}")
    log.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="SEO Lead Generation Pipeline"
    )
    parser.add_argument(
        "--city", required=True,
        help="City to target (e.g. 'Dallas TX')"
    )
    parser.add_argument(
        "--category", required=True,
        help="Business category (e.g. 'dentist')"
    )
    parser.add_argument(
        "--skip-scrape", action="store_true",
        help="Skip the Google Maps scraping step (reuse existing leads)"
    )
    parser.add_argument(
        "--scraper-mode", choices=["auto", "playwright", "serpapi"],
        default=None,
        help="Override SCRAPER_MODE: 'playwright' (free), 'serpapi' (paid), "
             "or 'auto' (try Playwright first, fall back to SerpAPI)"
    )
    args = parser.parse_args()

    run_pipeline(args.city, args.category, args.skip_scrape,
                 scraper_mode=args.scraper_mode)


if __name__ == "__main__":
    main()
