"""Central GitHub reporter: queries a GitHub user's repositories, gathers latest
workflow run statuses, renders an HTML report, and sends it via Gmail SMTP.

Usage: set environment variables `TARGET_GITHUB_USERNAME`, `RECIPIENTS` (comma-separated),
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, and `SMTP_PASS` (for Gmail: use app password).
Or leave empty to print report to stdout.
"""
import os
import requests
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")))


def github_get(path, params=None):
    url = f"https://api.github.com{path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if TOKEN:
        headers["Authorization"] = f"token {TOKEN}"
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


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
        return {"status": r.get("status"), "conclusion": r.get("conclusion"), "html_url": r.get("html_url"), "updated_at": updated_at, "name": r.get("name"), "run_started_at": started_at, "duration_seconds": duration}
    except requests.HTTPError:
        return None


def summarize_report(repos_report):
    total = len(repos_report)
    with_workflow = sum(1 for r in repos_report if r["status"] is not None)
    successful = sum(1 for r in repos_report if r["status"] and r["status"]["conclusion"] == "success")
    failed = sum(1 for r in repos_report if r["status"] and r["status"]["conclusion"] not in (None, "success"))
    in_progress = sum(1 for r in repos_report if r["status"] and r["status"]["status"] == "in_progress")
    no_workflow = total - with_workflow
    return {"total_repos": total, "with_workflow": with_workflow, "successful": successful, "failed": failed, "in_progress": in_progress, "no_workflow": no_workflow}


def gather_report(username):
    repos = list_repos(username)
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
    return report


def render_report(repos_report, summary):
    tmpl = env.get_template("report_github.html")
    return tmpl.render(repos=repos_report, summary=summary, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def render_dashboard(repos_report, summary):
    tmpl = env.get_template("dashboard.html")
    return tmpl.render(repos=repos_report, summary=summary, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def write_dashboard(repos_report, summary, path="dashboard.html"):
    html = render_dashboard(repos_report, summary)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved dashboard to {path}")
    return path


def create_issue(owner, repo, title, body, labels=None):
    if not TOKEN:
        print("Cannot create issue: GitHub token is not configured")
        return None

    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {TOKEN}",
    }
    payload = {"title": title, "body": body, "labels": labels or []}
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    if resp.status_code == 201:
        print(f"Created issue in {owner}/{repo}: {title}")
        return resp.json()
    else:
        print(f"Failed to create issue in {owner}/{repo}: {resp.status_code} {resp.text}")
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
        print("No recipients configured; printing report to stdout")
        print(html_body)
        return

    if not SMTP_HOST or not SMTP_PORT or not SMTP_USER or not SMTP_PASS:
        print("SMTP not configured; printing report to stdout")
        print(html_body)
        return

    # Validate and clean recipients
    valid_recipients = [r.strip() for r in recipients if r and "@" in r]
    if not valid_recipients:
        print(f"No valid recipients found in: {recipients}")
        print(html_body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ",".join(valid_recipients)
    part = MIMEText(html_body, "html")
    msg.attach(part)

    try:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, valid_recipients, msg.as_string())
        s.quit()
        print(f"Email sent successfully to {valid_recipients}")
    except Exception as e:
        print(f"SMTP error: {e}")
        raise


def main():
    if not GITHUB_USER:
        raise SystemExit("TARGET_GITHUB_USERNAME env var required")
    print(f"SMTP_HOST set: {bool(SMTP_HOST)}")
    print(f"SMTP_PORT set: {bool(SMTP_PORT)}")
    print(f"SMTP_USER set: {bool(SMTP_USER)}")
    print(f"SMTP_PASS set: {bool(SMTP_PASS)}")
    print(f"RECIPIENTS: {RECIPIENTS}")
    print(f"AUTO_CREATE_ISSUES: {AUTO_CREATE_ISSUES}")
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
        print(html)
    print(f"Dashboard available at: {dashboard_path}")


if __name__ == "__main__":
    main()
