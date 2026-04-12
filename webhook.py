"""Step 6 — Stripe Payment Flow + Flask Webhook + Report Delivery."""

import os
import stripe
import resend
from flask import Flask, request, jsonify, send_from_directory, abort

from models import get_db, update_lead, get_lead_by_email
from config import Config
from report_generator import generate_report, get_report_download_url
from logger import get_logger

log = get_logger("webhook")

app = Flask(__name__)

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


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


def run_server():
    port = int(os.environ.get("PORT", Config.FLASK_PORT))
    log.info(f"Starting webhook server on port {port}")
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    run_server()
