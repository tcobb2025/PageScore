"""Step 6 — Stripe Payment Flow + Flask Webhook + Report Delivery."""

import os
import json
import stripe
import resend
from flask import Flask, request, jsonify, send_from_directory, abort, render_template

from models import get_db, update_lead, get_lead_by_email
from config import Config
from report_generator import generate_report, get_report_download_url
from logger import get_logger

log = get_logger("webhook")

app = Flask(__name__, template_folder=Config.TEMPLATES_DIR)


def _score_color(score: int) -> tuple[str, str]:
    """Return (hex_color, label) based on score bucket."""
    if score <= 40:
        return "#dc2626", "Critical"
    if score <= 60:
        return "#f59e0b", "Needs Work"
    if score <= 80:
        return "#eab308", "Fair"
    return "#10b981", "Good"


def _findings_to_issues(findings: dict) -> list[dict]:
    """Convert raw SEO findings JSON into a ranked issues list."""
    issues = []

    if not findings.get("is_https", True):
        issues.append({
            "severity": "HIGH",
            "weight": 30,
            "text": "Your site is not using HTTPS — Chrome and Safari show "
                    "visitors a 'Not Secure' warning before they reach your content.",
        })

    status = findings.get("status_code")
    if status and status != 200:
        issues.append({
            "severity": "HIGH",
            "weight": 28,
            "text": f"Your homepage returns a {status} error, so some visitors "
                    "and search crawlers can't load your site at all.",
        })

    ps = findings.get("pagespeed_mobile")
    if ps is not None and ps < 50:
        issues.append({
            "severity": "HIGH",
            "weight": 22,
            "text": f"Your mobile speed score is {ps}/100 — visitors leave before "
                    "the page finishes loading, and Google demotes slow pages.",
        })

    meta = findings.get("meta_description")
    if meta in ("missing", "empty"):
        issues.append({
            "severity": "MEDIUM",
            "weight": 15,
            "text": "Your meta description is missing or empty, so Google is "
                    "guessing what snippet to show beneath your search listing.",
        })

    h1 = findings.get("h1_tag")
    if h1 == "missing":
        issues.append({
            "severity": "MEDIUM",
            "weight": 14,
            "text": "Your homepage has no H1 heading tag, which weakens how "
                    "Google understands the main topic of your page.",
        })
    elif h1 == "multiple":
        issues.append({
            "severity": "LOW",
            "weight": 8,
            "text": "Your homepage has multiple H1 tags, which dilutes the "
                    "ranking signal Google uses to identify your main topic.",
        })

    missing_alt = findings.get("images_missing_alt", 0)
    checked = findings.get("images_checked", 0)
    if checked and missing_alt:
        issues.append({
            "severity": "LOW",
            "weight": 6,
            "text": f"{missing_alt} of {checked} images on your homepage are "
                    "missing alt text, costing you Google Image Search traffic.",
        })

    issues.sort(key=lambda x: x["weight"], reverse=True)
    return issues


def _fallback_issues() -> list[dict]:
    return [
        {"severity": "HIGH", "text": "Critical on-page issues are limiting how "
                                     "Google crawls and ranks your site."},
        {"severity": "MEDIUM", "text": "Metadata gaps are hurting how your "
                                       "listings appear in search results."},
        {"severity": "LOW", "text": "Image and accessibility fixes are leaving "
                                    "easy ranking wins on the table."},
    ]

stripe.api_key = Config.STRIPE_SECRET_KEY
resend.api_key = Config.RESEND_API_KEY


def _deliver_report(lead_id: int, customer_email: str) -> bool:
    """Generate report and email the download link."""
    filepath = generate_report(lead_id)
    if not filepath:
        log.error(f"Failed to generate report for lead {lead_id}")
        return False

    download_url = get_report_download_url(filepath)

    try:
        resend.Emails.send({
            "from": Config.RESEND_FROM_EMAIL,
            "to": [customer_email],
            "subject": "Your SEO Audit Report is Ready",
            "html": f"""<div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #1e40af;">Your SEO Audit Report</h2>
                <p>Thank you for your purchase! Your full SEO audit report is ready.</p>
                <p>
                    <a href="{download_url}"
                       style="display: inline-block; background: #2563eb; color: white;
                              padding: 12px 24px; border-radius: 6px; text-decoration: none;
                              font-weight: bold;">
                        Download Your Report
                    </a>
                </p>
                <p style="color: #6b7280; font-size: 14px;">
                    This link will be available for 30 days.
                </p>
            </div>""",
        })

        conn = get_db()
        update_lead(conn, lead_id, report_delivered=1)
        conn.close()

        log.info(f"Report delivered to {customer_email}")
        return True

    except Exception as e:
        log.error(f"Failed to send report email to {customer_email}: {e}")
        return False


@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """Handle Stripe payment confirmation."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, Config.STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        log.error(f"Webhook signature verification failed: {e}")
        abort(400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email") or session.get(
            "customer_details", {}
        ).get("email")

        if not customer_email:
            log.error("No customer email in Stripe session")
            return jsonify({"error": "no email"}), 400

        log.info(f"Payment received from {customer_email}")

        conn = get_db()
        lead = get_lead_by_email(conn, customer_email)
        conn.close()

        if not lead:
            log.error(f"No lead found for email {customer_email}")
            return jsonify({"error": "lead not found"}), 404

        lead_id = lead["id"]

        conn = get_db()
        update_lead(
            conn, lead_id,
            paid=1,
            paid_at=session.get("created", ""),
            stripe_session_id=session.get("id", ""),
        )
        conn.close()

        _deliver_report(lead_id, customer_email)

    return jsonify({"status": "ok"}), 200


@app.route("/reports/<filename>")
def serve_report(filename):
    """Serve PDF reports for download."""
    if not filename.endswith(".pdf") or ".." in filename:
        abort(404)
    return send_from_directory(Config.REPORTS_DIR, filename, as_attachment=True)


@app.route("/report")
def report_page():
    """Render the personalized free SEO report landing page."""
    company = request.args.get("company", "Your Business").strip() or "Your Business"
    try:
        score = int(request.args.get("score", "0"))
    except ValueError:
        score = 0
    score = max(0, min(100, score))
    try:
        issues_count = int(request.args.get("issues", "3"))
    except ValueError:
        issues_count = 3
    email = request.args.get("email", "").strip().lower()

    issues_list: list[dict] = []
    if email:
        try:
            conn = get_db()
            lead = get_lead_by_email(conn, email)
            conn.close()
            if lead and lead["seo_findings"]:
                findings = json.loads(lead["seo_findings"])
                issues_list = _findings_to_issues(findings)[:3]
                if lead["seo_score"] is not None:
                    score = lead["seo_score"]
                if not company or company == "Your Business":
                    company = lead["business_name"] or company
        except Exception as e:
            log.warning(f"/report lookup failed for {email}: {e}")

    if not issues_list:
        issues_list = _fallback_issues()

    score_color, score_label = _score_color(score)

    return render_template(
        "report.html",
        company=company,
        score=score,
        score_color=score_color,
        score_label=score_label,
        issues_count=issues_count if issues_count > 0 else len(issues_list),
        issues_list=issues_list,
        stripe_link=Config.STRIPE_PAYMENT_LINK or "#",
    )


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


def run_server():
    port = int(os.environ.get("PORT", Config.FLASK_PORT))
    log.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run_server()
