import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")
    INSTANTLY_API_KEY = os.getenv("INSTANTLY_API_KEY", "")
    INSTANTLY_CAMPAIGN_ID = os.getenv("INSTANTLY_CAMPAIGN_ID", "")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PAYMENT_LINK = os.getenv("STRIPE_PAYMENT_LINK", "")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "reports@yourdomain.com")
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
    REPORT_BASE_URL = os.getenv("REPORT_BASE_URL", "http://localhost:5000/reports")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
    DAILY_EMAIL_LIMIT = int(os.getenv("DAILY_EMAIL_LIMIT", "50"))

    # Scraper config
    SCRAPER_MODE = os.getenv("SCRAPER_MODE", "auto")  # "playwright", "serpapi", or "auto"
    PROXY_URL = os.getenv("PROXY_URL", "")             # e.g. http://user:pass@proxy:8080
    SCRAPE_DELAY_MIN = float(os.getenv("SCRAPE_DELAY_MIN", "2.0"))
    SCRAPE_DELAY_MAX = float(os.getenv("SCRAPE_DELAY_MAX", "3.5"))

    DB_PATH = os.path.join(os.path.dirname(__file__), "leads.db")
    REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
    LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
    TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
