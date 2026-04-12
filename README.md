# SEO Lead Generation & Report Delivery System

Automated pipeline that scrapes local businesses from Google Maps, audits their websites for SEO issues, sends personalized cold emails to low-scoring businesses, and delivers full PDF reports on payment.

## Architecture

```
main.py (orchestrator)
  ├── scraper.py         → Google Maps via SerpAPI
  ├── email_extractor.py → Scrape websites for contact emails
  ├── seo_audit.py       → PageSpeed + on-page SEO checks
  ├── email_writer.py    → Claude generates personalized emails
  └── email_sender.py    → Send via Instantly.ai

webhook.py (Flask server)
  ├── /stripe-webhook    → Receives payment, triggers report
  ├── /reports/<file>    → Serves PDF downloads
  └── /health            → Health check

report_generator.py      → Claude writes report → WeasyPrint PDF
```

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

WeasyPrint requires system libraries. On macOS:
```bash
brew install pango
```

On Ubuntu/Debian:
```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys:
- `SERPAPI_KEY` — Get from [serpapi.com](https://serpapi.com)
- `ANTHROPIC_API_KEY` — Get from [console.anthropic.com](https://console.anthropic.com)
- `INSTANTLY_API_KEY` — Get from [instantly.ai](https://instantly.ai)
- `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` — Get from [dashboard.stripe.com](https://dashboard.stripe.com)
- `RESEND_API_KEY` — Get from [resend.com](https://resend.com)

Optional:
- `PAGESPEED_API_KEY` — Works without it but rate-limited

### 3. Create Stripe Payment Link

1. Go to Stripe Dashboard → Payment Links
2. Create a link for $79 one-time payment
3. In the payment link settings, collect customer email
4. Copy the URL and set it as `STRIPE_PAYMENT_LINK` in `.env`

### 4. Set up Stripe Webhook

1. Go to Stripe Dashboard → Webhooks
2. Add endpoint: `https://yourdomain.com/stripe-webhook`
3. Listen for `checkout.session.completed` events
4. Copy the signing secret to `STRIPE_WEBHOOK_SECRET` in `.env`

For local testing:
```bash
stripe listen --forward-to localhost:5000/stripe-webhook
```

### 5. Initialize database

```bash
python models.py
```

## Usage

### Run the full pipeline

```bash
python main.py --city "Dallas TX" --category "dentist"
```

### Run with existing leads (skip scraping)

```bash
python main.py --city "Dallas TX" --category "dentist" --skip-scrape
```

### Run individual steps

```bash
python scraper.py "Dallas TX" "dentist"
python email_extractor.py
python seo_audit.py
python email_writer.py
python email_sender.py
```

### Start the webhook server

```bash
python webhook.py
```

### Generate a report manually

```bash
python report_generator.py <lead_id>
```

### Run as a nightly cron job

```bash
# Edit crontab
crontab -e

# Add this line (runs at 2 AM daily)
0 2 * * * cd /path/to/seo-audit && /path/to/venv/bin/python main.py --city "Dallas TX" --category "dentist"
```

## Pipeline Flow

1. **Scrape** — Pulls businesses from Google Maps via SerpAPI
2. **Extract** — Visits each website to find contact emails
3. **Audit** — Runs 6 SEO checks, scores 0-100
4. **Write** — Claude generates personalized 4-sentence cold emails for scores < 60
5. **Send** — Delivers emails via Instantly.ai (max 50/day)
6. **Payment** — Stripe webhook triggers on purchase
7. **Report** — Claude writes full report, WeasyPrint renders PDF, Resend delivers

## SEO Scoring (0-100)

| Check | Max Deduction |
|-------|--------------|
| PageSpeed mobile score | 30 pts |
| Meta description | 15 pts |
| H1 tag | 15 pts |
| Image alt text | 15 pts |
| HTTPS | 15 pts |
| Homepage status | 10 pts |

Businesses scoring below 60 are flagged for outreach.

## Logs

Pipeline logs are written to `logs/pipeline_YYYY-MM-DD.log`.

## Database

SQLite database at `leads.db`. Key fields:
- Lead info (name, website, phone, city, category)
- Email extraction status
- SEO score and findings (JSON)
- Cold email text
- Send status and timestamps
- Payment status
- Report path and delivery status
