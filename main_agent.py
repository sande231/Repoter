"""MainAgent MVP: fetches metrics, aggregates, renders a report, and sends email.

Run once or schedule externally (Kubernetes CronJob). Expects ingestion server at INGESTION_URL.
"""
import logging
import os
import time
import statistics
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
from email_queue import enqueue as enqueue_email
from jinja2 import Environment, FileSystemLoader
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:5000")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587)) if os.environ.get("SMTP_PORT") else None
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RECIPIENTS = [r.strip() for r in os.environ.get("RECIPIENTS", "").split(",") if r.strip()]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
RETRY_ATTEMPTS = int(os.environ.get("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF = float(os.environ.get("RETRY_BACKOFF", "2.0"))

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")))

session = requests.Session()
retry_strategy = Retry(
    total=RETRY_ATTEMPTS,
    backoff_factor=RETRY_BACKOFF,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)


def fetch_agents():
    url = f"{INGESTION_URL.rstrip('/')}/agents"
    logger.debug("Fetching agents from %s", url)
    resp = session.get(url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def fetch_metrics(agent_id, since=None):
    url = f"{INGESTION_URL.rstrip('/')}/metrics/{agent_id}"
    params = {}
    if since:
        params["since"] = since
    logger.debug("Fetching metrics for %s from %s", agent_id, url)
    resp = session.get(url, params=params, timeout=5)
    resp.raise_for_status()
    return resp.json()


def aggregate_metrics(metrics_list):
    # Flatten numeric metrics and compute simple stats (avg, min, max)
    by_key = {}
    for entry in metrics_list:
        for k, v in (entry.get("metrics") or {}).items():
            try:
                val = float(v)
            except Exception:
                continue
            by_key.setdefault(k, []).append(val)
    stats = {}
    for k, vals in by_key.items():
        stats[k] = {
            "count": len(vals),
            "avg": statistics.mean(vals) if vals else None,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
        }
    return stats


def render_report(agents_report):
    tmpl = env.get_template("report.html")
    return tmpl.render(agents=agents_report, generated_at=int(time.time()))


def send_email(subject, html_body, recipients):
    # Deprecated in favor of durable queue; kept for backward compatibility.
    if not recipients:
        logger.warning("No recipients configured; printing report to stdout")
        print(html_body)
        return

    if not SMTP_HOST or not SMTP_PORT:
        logger.warning("SMTP not configured; printing report to stdout")
        print(html_body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or f"noreply@{SMTP_HOST}"
    msg["To"] = ",".join(recipients)
    part = MIMEText(html_body, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(msg["From"], recipients, msg.as_string())
        logger.info("Sent fallback email to %s", recipients)
    except Exception as exc:
        logger.error("Fallback email send failed: %s", exc)
        raise


def validate_env():
    if not INGESTION_URL:
        raise SystemExit("INGESTION_URL is required")
    if not RECIPIENTS:
        logger.warning("RECIPIENTS is empty; reports will be printed to stdout")


def run_once(window_seconds=3600):
    validate_env()
    logger.info("Collecting agent metrics from %s", INGESTION_URL)
    agents = fetch_agents()
    agents_report = []
    since = int(time.time()) - window_seconds
    for a in agents:
        agent_id = a.get("agent_id")
        try:
            metrics = fetch_metrics(agent_id, since=since)
        except Exception as exc:
            logger.warning("Failed to fetch metrics for %s: %s", agent_id, exc)
            metrics = []
        stats = aggregate_metrics(metrics)
        agents_report.append({"agent_id": agent_id, "info": a.get("info"), "stats": stats})

    html = render_report(agents_report)
    subject = f"Agent Performance Report - {time.strftime('%Y-%m-%d %H:%M:%S') }"
    enqueue_email(subject, html, RECIPIENTS)


def schedule(interval_seconds: int = None):
    """Start a background scheduler that runs `run_once` on the given interval."""
    if not interval_seconds:
        raise ValueError("interval_seconds required for scheduling")
    sched = BackgroundScheduler()
    sched.add_job(lambda: run_once(window_seconds=3600), 'interval', seconds=interval_seconds, next_run_time=None)
    sched.start()
    logger.info("Scheduled report every %s seconds.", interval_seconds)
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler")
        sched.shutdown()


if __name__ == "__main__":
    # If REPORT_INTERVAL_SECONDS is set, run scheduled reports using APScheduler.
    interval = None
    try:
        interval = int(os.environ.get("REPORT_INTERVAL_SECONDS"))
    except Exception:
        interval = None

    if interval:
        schedule(interval_seconds=interval)
    else:
        # default: run a single collection over last hour
        run_once(window_seconds=3600)
