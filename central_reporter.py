"""Central GitHub reporter: queries a GitHub user's repositories, gathers latest
workflow run statuses, renders an HTML report, and sends it via Gmail SMTP.

Usage: set environment variables `TARGET_GITHUB_USERNAME`, `RECIPIENTS` (comma-separated),
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, and `SMTP_PASS` (for Gmail: use app password).
Or leave empty to print report to stdout.
"""
import logging
import os
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from jinja2 import Environment, FileSystemLoader

GITHUB_USER = os.environ.get("TARGET_GITHUB_USERNAME")
TOKEN = os.environ.get("TARGET_GH_PAT") or os.environ.get("GITHUB_TOKEN")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587)) if os.environ.get("SMTP_PORT") else None
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")  # Remove spaces from Gmail app password
RECIPIENTS = [r.strip() for r in os.environ.get("RECIPIENTS", "").split(",") if r.strip()]
AUTO_CREATE_ISSUES = os.environ.get("AUTO_CREATE_ISSUES", "false").lower() in ("1", "true", "yes")
ISSUE_LABEL = os.environ.get("ISSUE_LABEL", "agent-report")
REPO_INCLUDE = [r.strip() for r in os.environ.get("REPO_INCLUDE", "").split(",") if r.strip()]
REPO_EXCLUDE = [r.strip() for r in os.environ.get("REPO_EXCLUDE", "").split(",") if r.strip()]
MAX_REPOS = int(os.environ.get("MAX_REPOS", "0")) if os.environ.get("MAX_REPOS") else None
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
RETRY_ATTEMPTS = int(os.environ.get("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF = float(os.environ.get("RETRY_BACKOFF", "2.0"))
SMTP_RETRY_ATTEMPTS = int(os.environ.get("SMTP_RETRY_ATTEMPTS", "2"))
SMTP_RETRY_BACKOFF = float(os.environ.get("SMTP_RETRY_BACKOFF", "2.0"))

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")))

session = requests.Session()
session.headers.update({"Accept": "application/vnd.github.v3+json"})
if TOKEN:
    session.headers["Authorization"] = f"token {TOKEN}"
retry_strategy = Retry(
    total=RETRY_ATTEMPTS,
    backoff_factor=RETRY_BACKOFF,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "PUT", "PATCH", "DELETE"],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)


def github_request(method, path, params=None, json_payload=None):
    url = f"https://api.github.com{path}"
    try:
        if method == "GET":
            resp = session.get(url, params=params, timeout=10)
        elif method == "POST":
            resp = session.post(url, params=params, json=json_payload, timeout=10)
        else:
            resp = session.request(method, url, params=params, json=json_payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        logger.error("GitHub request failed: %s %s %s", method, url, exc)
        raise


def github_get(path, params=None):
    return github_request("GET", path, params=params)


def list_repos(user):
    repos = []
    page = 1
    while True:
        data = github_get(f"/users/{user}/repos", params={"per_page": 100, "page": page, "sort": "updated"})
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def latest_workflow_status(owner, repo):
    try:
        data = github_get(f"/repos/{owner}/{repo}/actions/runs", params={"per_page": 1})
        runs = data.get("workflow_runs", [])
        if not runs:
            return None
        r = runs[0]
        started_at = r.get("run_started_at")
        updated_at = r.get("updated_at")
        duration = None
        if started_at and updated_at:
            try:
                start_ts = time.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
                update_ts = time.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
                duration = int(time.mktime(update_ts) - time.mktime(start_ts))
            except Exception:
                duration = None
        return {
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "html_url": r.get("html_url"),
            "updated_at": updated_at,
            "name": r.get("name"),
            "run_started_at": started_at,
            "duration_seconds": duration,
        }
    except requests.exceptions.RequestException:
        logger.warning("Unable to get workflow status for %s/%s", owner, repo)
        return None


def summarize_report(repos_report):
    total = len(repos_report)
    with_workflow = sum(1 for r in repos_report if r["status"] is not None)
    successful = sum(1 for r in repos_report if r["status"] and r["status"]["conclusion"] == "success")
    failed = sum(1 for r in repos_report if r["status"] and r["status"]["conclusion"] not in (None, "success"))
    in_progress = sum(1 for r in repos_report if r["status"] and r["status"]["status"] == "in_progress")
    no_workflow = total - with_workflow
    needs_attention = [r for r in repos_report if repo_health(r)["needs_attention"]]
    health_score = round((successful / with_workflow) * 100) if with_workflow else 0
    return {
        "total_repos": total,
        "with_workflow": with_workflow,
        "successful": successful,
        "failed": failed,
        "in_progress": in_progress,
        "no_workflow": no_workflow,
        "needs_attention": len(needs_attention),
        "health_score": health_score,
    }


def repo_health(repo):
    status = repo.get("status")
    if not status:
        return {
            "state": "no_workflow",
            "label": "No workflow",
            "needs_attention": False,
            "reason": "No GitHub Actions workflow runs found.",
        }

    workflow_status = status.get("status")
    conclusion = status.get("conclusion")
    if workflow_status in {"in_progress", "queued", "requested", "waiting", "pending"}:
        return {
            "state": "in_progress",
            "label": "In progress",
            "needs_attention": False,
            "reason": "Latest workflow run is still active.",
        }
    if workflow_status == "completed" and conclusion == "success":
        return {
            "state": "healthy",
            "label": "Healthy",
            "needs_attention": False,
            "reason": "Latest workflow run completed successfully.",
        }
    if workflow_status == "completed":
        return {
            "state": "attention",
            "label": "Needs attention",
            "needs_attention": True,
            "reason": f"Latest workflow completed with conclusion: {conclusion or 'unknown'}.",
        }
    return {
        "state": "unknown",
        "label": "Unknown",
        "needs_attention": True,
        "reason": f"Latest workflow status is {workflow_status or 'unknown'}.",
    }


def with_repo_health(repos_report):
    hydrated = []
    for repo in repos_report:
        repo_with_health = dict(repo)
        repo_with_health["health"] = repo.get("health") or repo_health(repo)
        hydrated.append(repo_with_health)
    return hydrated


def filter_repos(repos):
    if REPO_INCLUDE:
        logger.info("Filtering repos by allowlist: %s", REPO_INCLUDE)
        repos = [r for r in repos if r.get("name") in REPO_INCLUDE]
    if REPO_EXCLUDE:
        logger.info("Excluding repos: %s", REPO_EXCLUDE)
        repos = [r for r in repos if r.get("name") not in REPO_EXCLUDE]
    if MAX_REPOS:
        logger.info("Limiting repos to first %s entries", MAX_REPOS)
        repos = repos[:MAX_REPOS]
    return repos


def gather_report(username):
    repos = list_repos(username)
    repos = filter_repos(repos)
    report = []
    for r in repos:
        name = r.get("name")
        status = latest_workflow_status(username, name)
        report.append({
            "name": name,
            "html_url": r.get("html_url"),
            "description": r.get("description"),
            "stars": r.get("stargazers_count"),
            "forks": r.get("forks_count"),
            "private": r.get("private"),
            "updated_at": r.get("updated_at"),
            "status": status,
        })
    return with_repo_health(report)


def issue_exists(owner, repo, title):
    if not TOKEN:
        return False
    try:
        issues = github_get(
            f"/repos/{owner}/{repo}/issues",
            params={"state": "open", "labels": ISSUE_LABEL, "per_page": 100},
        )
        for issue in issues:
            if issue.get("title") == title:
                logger.info("Found existing issue for %s/%s: %s", owner, repo, title)
                return True
    except requests.exceptions.RequestException:
        logger.warning("Unable to check existing issues for %s/%s", owner, repo)
    return False


def render_report(repos_report, summary):
    tmpl = env.get_template("report_github.html")
    repos = with_repo_health(repos_report)
    attention_repos = [repo for repo in repos if repo["health"]["needs_attention"]]
    return tmpl.render(
        repos=repos,
        attention_repos=attention_repos,
        summary=summary,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def render_dashboard(repos_report, summary):
    tmpl = env.get_template("dashboard.html")
    repos = with_repo_health(repos_report)
    return tmpl.render(repos=repos, summary=summary, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def write_dashboard(repos_report, summary, path="dashboard.html"):
    html = render_dashboard(repos_report, summary)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved dashboard to {path}")
    return path


def create_issue(owner, repo, title, body, labels=None):
    if not TOKEN:
        logger.warning("Cannot create issue without a GitHub token")
        return None
    if issue_exists(owner, repo, title):
        logger.info("Skipping issue creation for %s/%s because duplicate exists", owner, repo)
        return None

    try:
        result = github_request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json_payload={"title": title, "body": body, "labels": labels or []},
        )
        logger.info("Created issue in %s/%s: %s", owner, repo, title)
        return result
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to create issue in %s/%s: %s", owner, repo, exc)
        return None


def build_issue_body(repo_name, status):
    body_lines = [
        f"The daily agent report detected a workflow failure in **{repo_name}**.",
        "",
        f"- Workflow name: {status.get('name', 'unknown')}",
        f"- Status: {status.get('status')}",
        f"- Conclusion: {status.get('conclusion')}",
        f"- Started at: {status.get('run_started_at') or 'n/a'}",
        f"- Finished at: {status.get('updated_at') or 'n/a'}",
        f"- Duration: {status.get('duration_seconds')} seconds" if status.get('duration_seconds') is not None else "- Duration: n/a",
        "",
        f"[View workflow run]({status.get('html_url')})",
        "",
        "Suggested action: review the failing workflow logs and fix the root cause."
    ]
    return "\n".join(body_lines)


def send_smtp(subject, html_body, recipients):
    """Send email via SMTP (Gmail or other SMTP provider)."""
    if not recipients:
        logger.warning("No recipients configured; printing report to stdout")
        print(html_body)
        return

    if not SMTP_HOST or not SMTP_PORT or not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP not configured; printing report to stdout")
        print(html_body)
        return

    valid_recipients = [r.strip() for r in recipients if r and "@" in r]
    if not valid_recipients:
        logger.warning("No valid recipients found in: %s", recipients)
        print(html_body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ",".join(valid_recipients)
    part = MIMEText(html_body, "html")
    msg.attach(part)

    attempt = 1
    while attempt <= SMTP_RETRY_ATTEMPTS:
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, valid_recipients, msg.as_string())
            logger.info("Email sent successfully to %s", valid_recipients)
            return
        except Exception as exc:
            logger.warning("SMTP attempt %s failed: %s", attempt, exc)
            if attempt == SMTP_RETRY_ATTEMPTS:
                logger.error("SMTP failed after %s attempts", SMTP_RETRY_ATTEMPTS)
                raise
            time.sleep(SMTP_RETRY_BACKOFF ** attempt)
            attempt += 1


def validate_env():
    if not GITHUB_USER:
        raise SystemExit("TARGET_GITHUB_USERNAME env var required")
    if AUTO_CREATE_ISSUES and not TOKEN:
        raise SystemExit("AUTO_CREATE_ISSUES requires TARGET_GH_PAT or GITHUB_TOKEN")
    if SMTP_USER and not SMTP_PASS:
        raise SystemExit("SMTP_USER is configured but SMTP_PASS is missing")


def main():
    validate_env()
    logger.info("SMTP_HOST set: %s", bool(SMTP_HOST))
    logger.info("SMTP_PORT set: %s", bool(SMTP_PORT))
    logger.info("SMTP_USER set: %s", bool(SMTP_USER))
    logger.info("SMTP_PASS set: %s", bool(SMTP_PASS))
    logger.info("RECIPIENTS: %s", RECIPIENTS)
    logger.info("AUTO_CREATE_ISSUES: %s", AUTO_CREATE_ISSUES)
    if TOKEN:
        logger.info("GitHub API token configured")
    else:
        logger.warning("GitHub API token missing; only public repo access will work")

    repos_report = gather_report(GITHUB_USER)
    summary = summarize_report(repos_report)
    dashboard_path = write_dashboard(repos_report, summary)
    html = render_report(repos_report, summary)
    subject = f"GitHub Agent Report for {GITHUB_USER} - {time.strftime('%Y-%m-%d') }"
    if AUTO_CREATE_ISSUES:
        for repo in repos_report:
            status = repo.get("status")
            if status and status.get("status") == "completed" and status.get("conclusion") != "success":
                issue_title = f"[Agent Report] Workflow failure in {repo['name']}"
                issue_body = build_issue_body(repo['name'], status)
                create_issue(GITHUB_USER, repo['name'], issue_title, issue_body, labels=[ISSUE_LABEL])
    if RECIPIENTS:
        send_smtp(subject, html, RECIPIENTS)
    else:
        logger.info("No recipients configured; printing report to stdout")
        print(html)
    logger.info("Dashboard available at: %s", dashboard_path)


if __name__ == "__main__":
    main()
