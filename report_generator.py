"""Step 7 — PDF Report Generator: create full SEO report on payment.

Produces a branded "Page Score" consulting-quality PDF using ReportLab.
"""

import json
import math
import os
import uuid
from datetime import datetime

import anthropic
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black, Color
from reportlab.lib.units import inch, mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether,
)
from reportlab.platypus.flowables import Flowable
from reportlab.graphics.shapes import Drawing, Wedge, Circle, String, Line
from reportlab.graphics import renderPDF

from models import get_db, update_lead, get_lead_by_id
from config import Config
from logger import get_logger

log = get_logger("report_generator")

os.makedirs(Config.REPORTS_DIR, exist_ok=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Brand & colour palette
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BRAND_NAME = "PAGE SCORE"

PRIMARY = HexColor("#1a1a2e")       # Charcoal — backgrounds, headings
ACCENT = HexColor("#c9a96e")        # Gold/copper — accent lines
SCORE_RED = HexColor("#e63946")     # Score < 40
SCORE_AMBER = HexColor("#f4a261")   # Score 40-69
SCORE_GREEN = HexColor("#2a9d8f")   # Score >= 70
TEXT_DARK = HexColor("#2d2d2d")     # Body copy
CARD_BG = HexColor("#f5f5f5")      # Finding card backgrounds
GRAY_MID = HexColor("#9ca3af")     # Secondary text, footers
WHITE = HexColor("#ffffff")
GRAY_RULE = HexColor("#e0e0e0")

PAGE_W, PAGE_H = letter  # 612 × 792


def _score_color(score: int) -> HexColor:
    if score < 40:
        return SCORE_RED
    if score < 70:
        return SCORE_AMBER
    return SCORE_GREEN


def _score_label(score: int) -> str:
    if score < 40:
        return "Critical"
    if score < 60:
        return "Needs Work"
    if score < 80:
        return "Fair"
    return "Good"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Paragraph styles
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _build_styles():
    base = getSampleStyleSheet()
    return {
        # ── Section headings ──
        "section_heading": ParagraphStyle(
            "SectionHeading", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=15, leading=20,
            textColor=PRIMARY, spaceBefore=4, spaceAfter=2,
        ),
        # ── Body ──
        "body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=10, leading=16,
            textColor=TEXT_DARK, spaceAfter=6,
        ),
        "body_bold": ParagraphStyle(
            "BodyBold", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=10, leading=16,
            textColor=TEXT_DARK, spaceAfter=6,
        ),
        # ── Dashboard ──
        "exec_summary": ParagraphStyle(
            "ExecSummary", parent=base["Normal"],
            fontName="Helvetica", fontSize=11, leading=17,
            textColor=TEXT_DARK, spaceAfter=8,
        ),
        "stat_label": ParagraphStyle(
            "StatLabel", parent=base["Normal"],
            fontName="Helvetica", fontSize=8, leading=10,
            textColor=GRAY_MID, alignment=TA_CENTER,
        ),
        "stat_value": ParagraphStyle(
            "StatValue", parent=base["Normal"],
            fontName="Helvetica-Bold", fontSize=11, leading=14,
            alignment=TA_CENTER,
        ),
        # ── Footer / header ──
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"],
            fontName="Helvetica", fontSize=7.5, leading=10,
            textColor=GRAY_MID, alignment=TA_CENTER,
        ),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Cover page — drawn via onFirstPage canvas callback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _draw_cover(canvas, doc, lead: dict, report_id: str):
    """Full-page charcoal cover with gold accent, drawn on the canvas."""
    c = canvas
    w, h = letter
    margin_l = doc.leftMargin
    margin_b = doc.bottomMargin

    c.saveState()

    # Full background
    c.setFillColor(PRIMARY)
    c.rect(0, 0, w, h, fill=1, stroke=0)

    # ── Brand name (top-left, spaced letterforms) ──
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 13)
    brand_x = margin_l
    brand_y = h - 1.8 * inch
    for char in BRAND_NAME:
        c.drawString(brand_x, brand_y, char)
        brand_x += 16 if char != " " else 10

    # ── Gold accent line ──
    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(margin_l, brand_y - 14, margin_l + 2.5 * inch, brand_y - 14)

    # ── Main title ──
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 38)
    c.drawString(margin_l, brand_y - 80, "Website Audit")
    c.drawString(margin_l, brand_y - 128, "Report")

    # ── Client name ──
    c.setFont("Helvetica", 20)
    c.setFillColor(HexColor("#d1d5db"))
    c.drawString(margin_l, brand_y - 180,
                 lead.get("business_name", ""))

    # ── Bottom section ──
    bottom_y = margin_b + 70

    # Gold line
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1)
    c.line(margin_l, bottom_y + 35, w - doc.rightMargin, bottom_y + 35)

    c.setFont("Helvetica", 9)
    c.setFillColor(GRAY_MID)
    c.drawString(margin_l, bottom_y + 8,
                 lead.get("website", ""))
    c.drawString(margin_l, bottom_y - 8,
                 f"Audit Date: {datetime.now().strftime('%B %d, %Y')}")
    c.drawString(margin_l, bottom_y - 24,
                 f"Report ID: PS-{report_id}")

    # Confidential tag
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(ACCENT)
    c.drawRightString(w - doc.rightMargin, bottom_y + 8, "CONFIDENTIAL")

    c.restoreState()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Custom Flowable: circular score gauge
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ScoreGauge(Flowable):
    """Circular arc gauge showing the SEO score."""

    DIAMETER = 160

    def __init__(self, score: int):
        super().__init__()
        self.score = score
        self.width = self.DIAMETER
        self.height = self.DIAMETER + 30  # extra for label

    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        cx = self.DIAMETER / 2
        cy = self.DIAMETER / 2 + 25  # offset up for label space
        r_outer = self.DIAMETER / 2 - 4
        r_inner = r_outer - 18
        color = _score_color(self.score)

        # Background track (full circle)
        c.saveState()
        c.setFillColor(HexColor("#e8e8e8"))
        c.setStrokeColor(HexColor("#e8e8e8"))
        c.setLineWidth(18)
        # Draw as a thick circle stroke
        c.circle(cx, cy, (r_outer + r_inner) / 2, fill=0, stroke=1)

        # Colored arc — goes from 90° (top) clockwise by score proportion
        angle_extent = (self.score / 100) * 360
        c.setStrokeColor(color)
        c.setLineWidth(18)
        c.setLineCap(1)  # Round cap

        # ReportLab arc: angles are counter-clockwise from 3 o'clock
        # We want clockwise from 12 o'clock (90°)
        start_angle = 90
        end_angle = 90 - angle_extent
        if angle_extent > 0:
            mid_r = (r_outer + r_inner) / 2
            c.arc(cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r,
                  end_angle, angle_extent)

        # Score number
        c.setFillColor(PRIMARY)
        c.setFont("Helvetica-Bold", 38)
        score_str = str(self.score)
        tw = c.stringWidth(score_str, "Helvetica-Bold", 38)
        c.drawString(cx - tw / 2, cy - 14, score_str)

        # "/100" below
        c.setFont("Helvetica", 11)
        c.setFillColor(GRAY_MID)
        tw2 = c.stringWidth("/100", "Helvetica", 11)
        c.drawString(cx - tw2 / 2, cy - 30, "/100")

        # Label below gauge
        label = _score_label(self.score)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(color)
        tw3 = c.stringWidth(label, "Helvetica-Bold", 10)
        c.drawString(cx - tw3 / 2, 8, label)

        c.restoreState()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers: stat boxes, severity pills, section bars
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _esc(text: str) -> str:
    """Escape XML-unsafe chars for ReportLab paragraphs."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _build_stat_box(label: str, value: str, color: HexColor,
                    styles: dict) -> Table:
    """Small stat card for the dashboard grid."""
    icon = Paragraph(
        f'<font color="{color.hexval()}" size="14">{_esc(value)}</font>',
        styles["stat_value"],
    )
    lbl = Paragraph(_esc(label), styles["stat_label"])
    t = Table([[icon], [lbl]], colWidths=[1.55 * inch], rowHeights=[24, 16])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("BOX", (0, 0), (-1, -1), 0.5, GRAY_RULE),
    ]))
    return t


def _severity_pill(level: str) -> Table:
    """Coloured pill indicating High / Medium / Low severity."""
    colors = {
        "high": SCORE_RED,
        "medium": SCORE_AMBER,
        "low": SCORE_GREEN,
    }
    bg = colors.get(level.lower(), GRAY_MID)
    p = Paragraph(
        f'<font color="#ffffff" size="7"><b>{_esc(level.upper())}</b></font>',
        ParagraphStyle("pill", alignment=TA_CENTER),
    )
    t = Table([[p]], colWidths=[52], rowHeights=[16])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROUNDEDCORNERS", [3, 3, 3, 3]),
    ]))
    return t


def _section_heading(text: str, styles: dict) -> list:
    """Section heading with left accent bar + gold underline."""
    heading_para = Paragraph(_esc(text), styles["section_heading"])

    bar_table = Table(
        [[None, heading_para]],
        colWidths=[4, None],
    )
    bar_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), PRIMARY),
        ("LEFTPADDING", (1, 0), (1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    return [
        Spacer(1, 16),
        bar_table,
        HRFlowable(width="40%", thickness=1.5, color=ACCENT,
                    spaceAfter=10, hAlign="LEFT"),
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Page header / footer callbacks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _header_footer(canvas, doc, is_cover=False):
    """Draw brand header + page footer on every non-cover page."""
    if is_cover:
        return
    canvas.saveState()
    w, h = letter

    # ── Header ──
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(PRIMARY)
    canvas.drawString(doc.leftMargin, h - 0.5 * inch, BRAND_NAME)

    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.75)
    canvas.line(doc.leftMargin, h - 0.55 * inch,
                w - doc.rightMargin, h - 0.55 * inch)

    # ── Footer ──
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRAY_MID)
    canvas.drawString(doc.leftMargin, 0.4 * inch, "Confidential")
    canvas.drawRightString(w - doc.rightMargin, 0.4 * inch,
                           f"Page {doc.page - 1}")  # -1 to skip cover

    canvas.setStrokeColor(GRAY_RULE)
    canvas.setLineWidth(0.5)
    canvas.line(doc.leftMargin, 0.48 * inch,
                w - doc.rightMargin, 0.48 * inch)

    canvas.restoreState()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dashboard: parse findings into stat boxes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _build_dashboard_stats(findings: dict, styles: dict) -> Table:
    """Build 2×3 grid of quick-stat boxes from audit findings."""
    checks = []

    # PageSpeed
    ps = findings.get("pagespeed_mobile")
    if ps is not None:
        c = SCORE_GREEN if ps >= 70 else (SCORE_AMBER if ps >= 50 else SCORE_RED)
        checks.append(("Mobile Speed", f"{ps}/100", c))
    else:
        checks.append(("Mobile Speed", "N/A", GRAY_MID))

    # HTTPS
    is_https = findings.get("is_https", True)
    checks.append(("HTTPS Secure",
                    "\u2713 Yes" if is_https else "\u2717 No",
                    SCORE_GREEN if is_https else SCORE_RED))

    # Meta description
    meta = findings.get("meta_description", "ok")
    if meta == "ok":
        checks.append(("Meta Description", "\u2713 Present", SCORE_GREEN))
    else:
        checks.append(("Meta Description", f"\u2717 {meta.title()}", SCORE_RED))

    # H1 tag
    h1 = findings.get("h1_tag", "ok")
    if h1 == "ok":
        checks.append(("H1 Heading", "\u2713 Present", SCORE_GREEN))
    else:
        checks.append(("H1 Heading", f"\u2717 {h1.title()}", SCORE_RED))

    # Alt text
    checked = findings.get("images_checked", 0)
    missing = findings.get("images_missing_alt", 0)
    if checked > 0:
        ok = checked - missing
        c = SCORE_GREEN if missing == 0 else (SCORE_AMBER if missing < checked else SCORE_RED)
        checks.append(("Image Alt Text", f"{ok}/{checked} pass", c))
    else:
        checks.append(("Image Alt Text", "No images", GRAY_MID))

    # Homepage status
    status = findings.get("status_code")
    if status == 200:
        checks.append(("Homepage Status", "\u2713 200 OK", SCORE_GREEN))
    elif status is not None:
        checks.append(("Homepage Status", f"\u2717 {status}", SCORE_RED))
    else:
        checks.append(("Homepage Status", "N/A", GRAY_MID))

    # Build 2×3 grid
    boxes = [_build_stat_box(lbl, val, col, styles) for lbl, val, col in checks]
    row1 = boxes[:3]
    row2 = boxes[3:6]

    grid = Table([row1, row2],
                 colWidths=[1.65 * inch] * 3,
                 rowHeights=[56, 56])
    grid.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return grid


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Claude content generation (unchanged)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _generate_report_content(lead: dict) -> str:
    """Use Claude to write the full report in plain English."""
    findings = json.loads(lead["seo_findings"]) if lead["seo_findings"] else {}

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    prompt = f"""Write a professional SEO audit report for a local business website.

Business name: {lead['business_name']}
Category: {lead['category']}
City: {lead['city']}
Website: {lead['website']}
Overall SEO score: {lead['seo_score']}/100
Audit date: {datetime.now().strftime('%B %d, %Y')}

Technical findings (raw data):
{json.dumps(findings, indent=2)}

Write the report with these sections (use EXACTLY these headings on their own line):

EXECUTIVE SUMMARY
2-3 sentences about overall website health.

SCORE BREAKDOWN
Explain what the {lead['seo_score']}/100 score means in practical terms.

DETAILED FINDINGS
For each issue found, write a paragraph that includes:
- The issue name
- Severity tag in brackets like [HIGH], [MEDIUM], or [LOW]
- Why it matters for their business specifically (tie it to {lead['category']} in {lead['city']})
- Exactly how to fix it, step by step, written so a non-technical business owner can understand
- If the finding is "ok", briefly note it as a positive with [PASS]

PRIORITY ACTION PLAN
A numbered list of what to fix first, second, third, etc.
Order by impact: highest impact fixes first.

NEXT STEPS
2-3 sentences about what they can do to improve going forward.

Write in a professional but accessible tone. Avoid technical jargon.
Use specific, actionable language. Reference their business category and city where relevant.
Do NOT use any markdown formatting (no **, ##, etc). Just plain text with the section headings."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text.strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main PDF renderer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _render_pdf(lead: dict, report_text: str) -> str:
    """Render a branded consulting-quality PDF. Returns file path."""
    report_id = uuid.uuid4().hex[:12].upper()
    filename = f"seo_report_{report_id.lower()}.pdf"
    filepath = os.path.join(Config.REPORTS_DIR, filename)
    findings = json.loads(lead["seo_findings"]) if lead.get("seo_findings") else {}

    styles = _build_styles()
    score = lead.get("seo_score", 0)

    doc = SimpleDocTemplate(
        filepath, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.65 * inch,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
    )
    story: list = []

    # ━━ PAGE 1: Cover (drawn via canvas callback, just need a PageBreak) ━━
    story.append(Spacer(1, 0.01))  # Tiny flowable so page 1 is emitted
    story.append(PageBreak())

    # ━━ PAGE 2: Score Dashboard ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    story.append(Spacer(1, 10))

    # Title row
    story.extend(_section_heading("Performance Overview", styles))

    # Gauge + stats side by side
    gauge = ScoreGauge(score)
    stats_grid = _build_dashboard_stats(findings, styles)

    dashboard = Table(
        [[gauge, stats_grid]],
        colWidths=[2.4 * inch, None],
    )
    dashboard.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, -1), 20),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
    ]))
    story.append(dashboard)
    story.append(Spacer(1, 16))

    # ── Parse report text into sections ──
    SECTION_HEADINGS = {
        "EXECUTIVE SUMMARY", "SCORE BREAKDOWN", "DETAILED FINDINGS",
        "PRIORITY ACTION PLAN", "NEXT STEPS",
    }

    sections: dict[str, list[str]] = {}
    current_section = None

    for line in report_text.split("\n"):
        stripped = line.strip()
        matched = False
        for h in SECTION_HEADINGS:
            if stripped.upper().startswith(h):
                current_section = h
                sections[current_section] = []
                matched = True
                break
        if not matched and current_section is not None:
            sections.setdefault(current_section, []).append(line)

    # ── Executive Summary (on dashboard page) ──
    if "EXECUTIVE SUMMARY" in sections:
        story.extend(_section_heading("Executive Summary", styles))
        for line in sections["EXECUTIVE SUMMARY"]:
            if line.strip():
                story.append(Paragraph(_esc(line.strip()),
                                       styles["exec_summary"]))

    story.append(PageBreak())

    # ━━ PAGE 3+: Detailed Content ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Score Breakdown
    if "SCORE BREAKDOWN" in sections:
        story.extend(_section_heading("Score Breakdown", styles))
        for line in sections["SCORE BREAKDOWN"]:
            if line.strip():
                story.append(Paragraph(_esc(line.strip()), styles["body"]))

    # Detailed Findings — render as cards
    if "DETAILED FINDINGS" in sections:
        story.extend(_section_heading("Detailed Findings", styles))
        story.append(Spacer(1, 6))

        current_finding_lines: list[str] = []

        def flush_finding():
            if not current_finding_lines:
                return
            text = " ".join(current_finding_lines).strip()
            if not text:
                return

            # Detect severity from brackets
            severity = "info"
            for tag, level in [("[HIGH]", "High"), ("[MEDIUM]", "Medium"),
                               ("[LOW]", "Low"), ("[PASS]", "Pass")]:
                if tag in text.upper():
                    severity = level
                    text = text.replace(tag, "").replace(tag.lower(), "")
                    text = text.replace(tag.title(), "")
                    break

            sev_colors = {
                "High": SCORE_RED, "Medium": SCORE_AMBER,
                "Low": SCORE_GREEN, "Pass": SCORE_GREEN, "info": GRAY_MID,
            }

            # Build card
            pill = _severity_pill(severity)
            body = Paragraph(_esc(text.strip()), styles["body"])

            card = Table(
                [[pill, body]],
                colWidths=[62, None],
            )
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
                ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                ("BOX", (0, 0), (-1, -1), 0.5, GRAY_RULE),
                ("VALIGN", (0, 0), (0, -1), "TOP"),
                ("VALIGN", (1, 0), (1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (0, -1), 8),
                ("LEFTPADDING", (1, 0), (1, -1), 10),
                ("RIGHTPADDING", (1, 0), (1, -1), 12),
            ]))

            story.append(KeepTogether([card, Spacer(1, 8)]))

        for line in sections["DETAILED FINDINGS"]:
            stripped = line.strip()
            if not stripped:
                flush_finding()
                current_finding_lines = []
            else:
                current_finding_lines.append(stripped)
        flush_finding()

    # Priority Action Plan
    if "PRIORITY ACTION PLAN" in sections:
        story.extend(_section_heading("Priority Action Plan", styles))
        for line in sections["PRIORITY ACTION PLAN"]:
            stripped = line.strip()
            if stripped:
                # Bold the number prefix if present
                if stripped[0].isdigit() and "." in stripped[:4]:
                    parts = stripped.split(".", 1)
                    num = parts[0].strip()
                    rest = parts[1].strip() if len(parts) > 1 else ""
                    story.append(Paragraph(
                        f'<font color="{ACCENT.hexval()}"><b>{_esc(num)}.</b></font>'
                        f'&nbsp;&nbsp;{_esc(rest)}',
                        styles["body"],
                    ))
                else:
                    story.append(Paragraph(_esc(stripped), styles["body"]))

    # Next Steps
    if "NEXT STEPS" in sections:
        story.extend(_section_heading("Next Steps", styles))
        for line in sections["NEXT STEPS"]:
            if line.strip():
                story.append(Paragraph(_esc(line.strip()), styles["body"]))

    # ── Build with page callbacks ──
    def on_first_page(canvas_obj, doc_obj):
        _draw_cover(canvas_obj, doc_obj, lead, report_id)

    def on_later_pages(canvas_obj, doc_obj):
        _header_footer(canvas_obj, doc_obj)

    doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)
    log.info(f"  PDF generated: {filepath}")
    return filepath


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API (unchanged)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def generate_report(lead_id: int) -> str | None:
    """Generate full PDF report for a lead. Returns file path or None."""
    conn = get_db()
    lead = get_lead_by_id(conn, lead_id)

    if not lead:
        log.error(f"Lead {lead_id} not found")
        conn.close()
        return None

    lead = dict(lead)
    log.info(f"Generating report for: {lead['business_name']}")

    try:
        report_text = _generate_report_content(lead)
        filepath = _render_pdf(lead, report_text)

        update_lead(conn, lead_id, report_path=filepath)
        conn.close()
        return filepath

    except Exception as e:
        log.error(f"Error generating report for lead {lead_id}: {e}")
        conn.close()
        return None


def get_report_download_url(filepath: str) -> str:
    """Build a download URL for a report file."""
    filename = os.path.basename(filepath)
    return f"{Config.REPORT_BASE_URL}/{filename}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python report_generator.py <lead_id>")
        sys.exit(1)
    lead_id = int(sys.argv[1])
    path = generate_report(lead_id)
    if path:
        print(f"Report generated: {path}")
        print(f"Download URL: {get_report_download_url(path)}")
    else:
        print("Report generation failed")
