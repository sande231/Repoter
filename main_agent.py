"""MainAgent MVP: fetches metrics, aggregates, renders a report, and sends email.

UPDATED: Integrated with structured logging, graceful shutdown, and health monitoring.

Run once or schedule externally (Kubernetes CronJob). Expects ingestion server at INGESTION_URL.
"""
import logging
import os
import time
import statistics
import smtplib
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
from email_queue import enqueue as enqueue_email
from jinja2 import Environment, FileSystemLoader
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# NEW: Import structured logging and graceful shutdown
from structured_logger import StructuredLogger
from graceful_shutdown import GracefulShutdownHandler
from agent_health_monitor import AgentHealthMonitor

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:5000")
INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY")
INGESTION_API_KEY_HEADER = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587)) if os.environ.get("SMTP_PORT") else None
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RECIPIENTS = [r.strip() for r in os.environ.get("RECIPIENTS", "").split(",") if r.strip()]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
RETRY_ATTEMPTS = int(os.environ.get("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF = float(os.environ.get("RETRY_BACKOFF", "2.0"))
HEARTBEAT_THRESHOLD_SECONDS = int(os.environ.get("HEARTBEAT_THRESHOLD_SECONDS", "600"))

# NEW: Use StructuredLogger instead of basicConfig
logger = StructuredLogger(agent_id="main_agent", level=LOG_LEVEL)

# NEW: Initialize health monitor for this agent
health_monitor = AgentHealthMonitor(agent_id="main_agent")

# NEW: Initialize graceful shutdown handler
shutdown_handler = GracefulShutdownHandler(timeout_seconds=30, logger=logger)

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


def _auth_headers() -> dict:
    headers = {}
    if INGESTION_API_KEY:
        headers[INGESTION_API_KEY_HEADER] = INGESTION_API_KEY
    return headers


def extract_agent_info(agent_record):
    """Return the user-provided registration info from an ingestion agent record."""
    info = (agent_record or {}).get("info") or {}
    nested_info = info.get("info") if isinstance(info, dict) else None
    return nested_info if isinstance(nested_info, dict) else info


def fetch_agents():
    url = f"{INGESTION_URL.rstrip('/')}/agents"
    logger.log_event("fetching_agents", url=url)
    try:
        resp = session.get(url, headers=_auth_headers(), timeout=5)
        resp.raise_for_status()
        health_monitor.record_task_completion()
        return resp.json()
    except Exception as e:
        logger.log_error(e, {"action": "fetch_agents", "url": url})
        health_monitor.record_error()
        raise


def fetch_metrics(agent_id, since=None):
    url = f"{INGESTION_URL.rstrip('/')}/metrics/{agent_id}"
    params = {}
    if since:
        params["since"] = since
    logger.log_event("fetching_metrics", agent_id=agent_id, url=url)
    try:
        resp = session.get(url, params=params, headers=_auth_headers(), timeout=5)
        resp.raise_for_status()
        health_monitor.record_task_completion()
        return resp.json()
    except Exception as e:
        logger.log_error(e, {"action": "fetch_metrics", "agent_id": agent_id})
        health_monitor.record_error()
        raise


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


def parse_agent_problems(metrics_list):
    problems = []
    for entry in metrics_list:
        metrics = entry.get("metrics") or {}
        status = str(metrics.get("status", "")).lower()
        if status in {"problem", "error", "degraded"} or metrics.get("problem"):
            problem_payload = metrics.get("problem") or {}
            if isinstance(problem_payload, str):
                problem_payload = {"message": problem_payload}
            if not isinstance(problem_payload, dict):
                problem_payload = {"message": str(problem_payload)}
            problems.append({
                "timestamp": entry.get("timestamp"),
                "status": status or "problem",
                **problem_payload,
            })
    return problems


def attempt_fix_agent(agent_info, agent_id):
    control_url = (agent_info or {}).get("control_url")
    result = {"success": False, "agent_id": agent_id, "attempts": []}
    if not control_url:
        result["reason"] = "missing control_url"
        return result

    base_url = control_url.rstrip("/")
    endpoints = [base_url, f"{base_url}/fix", f"{base_url}/restart", f"{base_url}/self_heal"]
    for endpoint in endpoints:
        attempt = {"endpoint": endpoint}
        try:
            logger.log_event("attempting_agent_fix", agent_id=agent_id, endpoint=endpoint)
            resp = session.post(endpoint, timeout=5)
            attempt["status_code"] = resp.status_code
            attempt["ok"] = resp.ok
            result["attempts"].append(attempt)
            if resp.ok:
                result["success"] = True
                result["endpoint"] = endpoint
                logger.log_event("agent_fix_succeeded", agent_id=agent_id, endpoint=endpoint)
                health_monitor.record_task_completion()
                return result
        except Exception as exc:
            attempt["error"] = str(exc)
            result["attempts"].append(attempt)
            logger.log_error(exc, {"agent_id": agent_id, "endpoint": endpoint})
    result["reason"] = "no successful remediation endpoint"
    health_monitor.record_error()
    return result


def find_stale_agents(agents, threshold_seconds):
    now = int(time.time())
    stale = []
    for a in agents:
        last_seen = a.get("info", {}).get("last_seen") or a.get("last_seen")
        if last_seen is None:
            continue
        age = now - int(last_seen)
        if age >= threshold_seconds:
            stale.append({
                "agent_id": a.get("agent_id"),
                "last_seen": int(last_seen),
                "age_seconds": age,
            })
    return stale


def render_report(agents_report, stale_agents=None):
    tmpl = env.get_template("report.html")
    return tmpl.render(
        agents=agents_report,
        stale_agents=stale_agents or [],
        generated_at=int(time.time()),
    )


def send_email(subject, html_body, recipients):
    # Deprecated in favor of durable queue; kept for backward compatibility.
    if not recipients:
        logger.log_event("email_no_recipients", subject=subject)
        print(html_body)
        return

    if not SMTP_HOST or not SMTP_PORT:
        logger.log_event("email_smtp_not_configured", subject=subject)
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
        logger.log_event("email_sent", recipients=recipients, subject=subject)
        health_monitor.record_task_completion()
    except Exception as exc:
        logger.log_error(exc, {"action": "send_email", "subject": subject})
        health_monitor.record_error()
        raise


def validate_env():
    if not INGESTION_URL:
        raise SystemExit("INGESTION_URL is required")
    if not RECIPIENTS:
        logger.log_event("warning_no_recipients")


def run_once(window_seconds=3600):
    validate_env()
    logger.log_event("report_cycle_started", ingestion_url=INGESTION_URL)
    
    try:
        agents = fetch_agents()
        agents_report = []
        since = int(time.time()) - window_seconds
        any_problems = False

        for a in agents:
            agent_id = a.get("agent_id")
            try:
                metrics = fetch_metrics(agent_id, since=since)
            except Exception as exc:
                logger.log_event("metrics_fetch_failed", agent_id=agent_id)
                metrics = []

            stats = aggregate_metrics(metrics)
            problems = parse_agent_problems(metrics)
            fix_result = None
            if problems:
                any_problems = True
                logger.log_event("agent_problems_detected", agent_id=agent_id, problem_count=len(problems))
                fix_result = attempt_fix_agent(extract_agent_info(a), agent_id)
                if fix_result.get("success"):
                    logger.log_event("remediation_succeeded", agent_id=agent_id, endpoint=fix_result.get("endpoint"))
                else:
                    logger.log_event("remediation_failed", agent_id=agent_id, reason=fix_result.get("reason"))

            agents_report.append({
                "agent_id": agent_id,
                "info": extract_agent_info(a),
                "stats": stats,
                "problems": problems,
                "fix_result": fix_result,
            })

        stale_agents = find_stale_agents(agents, HEARTBEAT_THRESHOLD_SECONDS)
        if stale_agents:
            logger.log_event("stale_agents_detected", count=len(stale_agents))

        html = render_report(agents_report, stale_agents=stale_agents)
        subject = f"Agent Performance Report - {time.strftime('%Y-%m-%d %H:%M:%S')}"
        if stale_agents or any_problems:
            alert_count = len(stale_agents) if stale_agents else 0
            problem_suffix = f" + {sum(len(r.get('problems', [])) for r in agents_report)} problem(s)" if any_problems else ""
            subject = f"[ALERT] {subject} ({alert_count} stale agents{problem_suffix})"
        
        enqueue_email(subject, html, RECIPIENTS)
        logger.log_event("report_cycle_completed", subject=subject)
        health_monitor.record_task_completion()
        
    except Exception as e:
        logger.log_error(e, {"action": "report_cycle"})
        health_monitor.record_error()
        raise


def schedule(interval_seconds: int = None):
    """Start a background scheduler that runs `run_once` on the given interval."""
    if not interval_seconds:
        raise ValueError("interval_seconds required for scheduling")
    sched = BackgroundScheduler()
    sched.add_job(lambda: run_once(window_seconds=3600), 'interval', seconds=interval_seconds, next_run_time=None)
    sched.start()
    logger.log_event("scheduler_started", interval_seconds=interval_seconds)
    
    # NEW: Register shutdown callback to stop scheduler gracefully
    def shutdown_callback():
        logger.log_event("scheduler_shutting_down")
        sched.shutdown()
    
    shutdown_handler.register_shutdown_callback(shutdown_callback)
    
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.log_event("shutdown_signal_received")
        shutdown_handler._shutting_down = True


# NEW: Add endpoint to check agent health
def get_agent_health():
    """Get health status of the main_agent"""
    health = health_monitor.get_health_check()
    return {
        "agent_id": "main_agent",
        "status": health.status,
        "uptime_seconds": health.uptime_seconds,
        "error_rate_percent": health.error_rate_percent,
        "metrics": health.metrics
    }


if __name__ == "__main__":
    # NEW: Start graceful shutdown handler
    shutdown_handler.start()
    
    # If REPORT_INTERVAL_SECONDS is set, run scheduled reports using APScheduler.
    interval = None
    try:
        interval = int(os.environ.get("REPORT_INTERVAL_SECONDS"))
    except Exception:
        interval = None

    try:
        if interval:
            schedule(interval_seconds=interval)
        else:
            # default: run a single collection over last hour
            run_once(window_seconds=3600)
    finally:
        logger.log_event("main_agent_exiting")