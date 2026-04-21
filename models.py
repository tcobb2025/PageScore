import sqlite3
import os
from config import Config


def get_db():
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            website TEXT,
            phone TEXT,
            maps_url TEXT,
            city TEXT,
            category TEXT,
            email TEXT,
            email_status TEXT DEFAULT 'pending',
            email_confidence TEXT DEFAULT NULL,
            seo_score INTEGER,
            seo_findings TEXT,
            flagged INTEGER DEFAULT 0,
            cold_email TEXT,
            email_sent INTEGER DEFAULT 0,
            email_sent_at TEXT,
            paid INTEGER DEFAULT 0,
            paid_at TEXT,
            stripe_session_id TEXT,
            report_path TEXT,
            report_delivered INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(website)
        );

        CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
        CREATE INDEX IF NOT EXISTS idx_leads_flagged ON leads(flagged);
        CREATE INDEX IF NOT EXISTS idx_leads_email_sent ON leads(email_sent);
        CREATE INDEX IF NOT EXISTS idx_leads_city_category ON leads(city, category);
    """)
    # Migration: add email_confidence column to existing databases
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN email_confidence TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Migration: add subject_variant column for A/B subject-line testing
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN subject_variant TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Migration: add first_name column
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN first_name TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    # Migration: add Instantly campaign tracking columns
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN instantly_lead_id TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN added_to_campaign INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN campaign_added_at TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    conn.close()


def insert_lead(conn, lead: dict) -> int | None:
    """Insert a lead, skip if website already exists. Returns row id or None."""
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO leads
               (business_name, website, phone, maps_url, city, category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (lead["business_name"], lead["website"], lead.get("phone"),
             lead.get("maps_url"), lead.get("city"), lead.get("category"))
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    except sqlite3.Error:
        return None


def update_lead(conn, lead_id: int, **fields):
    """Update arbitrary fields on a lead."""
    fields["updated_at"] = "datetime('now')"
    set_clause = ", ".join(
        f"{k} = {v}" if v == "datetime('now')" else f"{k} = ?"
        for k, v in fields.items()
    )
    values = [v for v in fields.values() if v != "datetime('now')"]
    values.append(lead_id)
    conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", values)
    conn.commit()


def get_leads_needing_email(conn):
    """Leads with a website but no email extracted yet."""
    return conn.execute(
        "SELECT * FROM leads WHERE website IS NOT NULL AND email IS NULL AND email_status = 'pending'"
    ).fetchall()


def get_leads_needing_audit(conn):
    """Leads with email but no SEO score yet."""
    return conn.execute(
        "SELECT * FROM leads WHERE email IS NOT NULL AND seo_score IS NULL AND email_status != 'skip'"
    ).fetchall()


def get_flagged_leads_needing_email_copy(conn):
    """Flagged leads that don't have a cold email written yet."""
    return conn.execute(
        "SELECT * FROM leads WHERE flagged = 1 AND cold_email IS NULL"
    ).fetchall()


def get_leads_ready_to_send(conn, limit: int = 50):
    """Leads with cold email written but not sent yet."""
    return conn.execute(
        "SELECT * FROM leads WHERE cold_email IS NOT NULL AND email_sent = 0 LIMIT ?",
        (limit,)
    ).fetchall()


def get_lead_by_email(conn, email: str):
    return conn.execute("SELECT * FROM leads WHERE email = ?", (email,)).fetchone()


def get_lead_by_id(conn, lead_id: int):
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


def count_emails_sent_today(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM leads WHERE email_sent_at >= date('now')"
    ).fetchone()[0]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {Config.DB_PATH}")
