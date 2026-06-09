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
        data = github_get(f"/users/{user}/repos", params={"per_page": 100, "page": page})
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
        return {"status": r.get("status"), "conclusion": r.get("conclusion"), "html_url": r.get("html_url"), "updated_at": r.get("updated_at")}
    except requests.HTTPError:
        return None


def gather_report(username):
    repos = list_repos(username)
    report = []
    for r in repos:
        name = r.get("name")
        status = latest_workflow_status(username, name)
        report.append({"name": name, "html_url": r.get("html_url"), "status": status})
    return report


def render_report(repos_report):
    tmpl = env.get_template("report_github.html")
    return tmpl.render(repos=repos_report, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))


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
    repos_report = gather_report(GITHUB_USER)
    html = render_report(repos_report)
    subject = f"GitHub Agent Report for {GITHUB_USER} - {time.strftime('%Y-%m-%d') }"
    if RECIPIENTS:
        send_smtp(subject, html, RECIPIENTS)
    else:
        print(html)


if __name__ == "__main__":
    main()
