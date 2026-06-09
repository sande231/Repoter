"""Durable email queue backed by SQLite.

Provides `enqueue(subject, body, recipients, delay_seconds=0)` and a
`worker_loop(poll_interval=10)` that dequeues due emails and sends them with retries.
"""
import os
import sqlite3
import time
import json
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

DB_PATH = os.environ.get("EMAIL_QUEUE_DB", os.path.join(os.path.dirname(__file__), "email_queue.db"))

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587)) if os.environ.get("SMTP_PORT") else None
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY,
            subject TEXT,
            body TEXT,
            recipients TEXT,
            status TEXT,
            attempts INTEGER,
            last_error TEXT,
            created_at INTEGER,
            next_try INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


def enqueue(subject: str, body: str, recipients, delay_seconds: int = 0):
    """Add an email to the durable queue."""
    _init_db()
    if isinstance(recipients, (list, tuple)):
        rec = ",".join([r.strip() for r in recipients if r])
    else:
        rec = str(recipients or "")
    now = int(time.time())
    next_try = now + int(delay_seconds or 0)
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO email_queue (subject, body, recipients, status, attempts, created_at, next_try) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (subject, body, rec, "queued", 0, now, next_try),
    )
    conn.commit()
    conn.close()


def _send_via_smtp(from_addr, recipients, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ",".join(recipients)
    part = MIMEText(html_body, "html")
    msg.attach(part)

    s = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
    s.starttls()
    if SMTP_USER and SMTP_PASS:
        s.login(SMTP_USER, SMTP_PASS)
    s.sendmail(from_addr, recipients, msg.as_string())
    s.quit()


def _attempt_send(row):
    recipients = [r.strip() for r in row["recipients"].split(",") if r.strip()]
    subject = row["subject"]
    body = row["body"]
    from_addr = SMTP_USER or f"noreply@{SMTP_HOST or 'localhost'}"

    if not SMTP_HOST or not SMTP_PORT:
        # fallback: print to stdout
        print(f"[EMAIL QUEUE] Would send to {recipients}: {subject}\n{body[:200]}...")
        return True, None

    try:
        _send_via_smtp(from_addr, recipients, subject, body)
        return True, None
    except Exception as e:
        return False, str(e)


def worker_loop(poll_interval: int = 10):
    _init_db()
    conn = _get_conn()
    c = conn.cursor()
    print("Email queue worker started, DB=", DB_PATH)
    try:
        while True:
            now = int(time.time())
            # select due emails
            c.execute("SELECT * FROM email_queue WHERE status IN ('queued','retry') AND next_try <= ? ORDER BY created_at LIMIT 10", (now,))
            rows = c.fetchall()
            if not rows:
                time.sleep(poll_interval)
                continue

            for row in rows:
                id = row["id"]
                attempts = row["attempts"] or 0
                ok, err = _attempt_send(row)
                if ok:
                    c.execute("UPDATE email_queue SET status=?, attempts=? WHERE id=?", ("sent", attempts + 1, id))
                    conn.commit()
                    print(f"Email id={id} sent")
                else:
                    attempts += 1
                    backoff = (2 ** attempts) * 60
                    next_try = int(time.time()) + backoff
                    c.execute("UPDATE email_queue SET status=?, attempts=?, last_error=?, next_try=? WHERE id=?", ("retry", attempts, err, next_try, id))
                    conn.commit()
                    print(f"Email id={id} failed attempt={attempts}, next_try={next_try}, err={err}")

    finally:
        conn.close()


if __name__ == "__main__":
    # run worker loop
    worker_loop()
