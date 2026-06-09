"""Central GitHub reporter: queries a GitHub user's repositories, gathers latest
workflow run statuses, renders an HTML report, and sends it via SendGrid.

Usage: set environment variables `TARGET_GITHUB_USERNAME`, `RECIPIENTS` (comma-separated),
and `SENDGRID_API_KEY` (or leave unset to print report). Run in a scheduled GitHub Actions workflow.
"""
import os
import requests
import time
from jinja2 import Environment, FileSystemLoader

GITHUB_USER = os.environ.get("TARGET_GITHUB_USERNAME")
TOKEN = os.environ.get("TARGET_GH_PAT") or os.environ.get("GITHUB_TOKEN")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
RECIPIENTS = [r.strip() for r in os.environ.get("RECIPIENTS", "").split(",") if r.strip()]
FROM_EMAIL = os.environ.get("SENDGRID_FROM", f"noreply@{GITHUB_USER or 'example.com'}")

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


def send_sendgrid(subject, html_body, recipients):
    if not SENDGRID_API_KEY:
        print("SENDGRID_API_KEY not set; printing report to stdout")
        print(html_body)
        return

    url = "https://api.sendgrid.com/v3/mail/send"
    headers = {"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}
    personalizations = [{"to": [{"email": r} for r in recipients]}]
    payload = {
        "personalizations": personalizations,
        "from": {"email": FROM_EMAIL},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    resp.raise_for_status()
    return resp


def main():
    if not GITHUB_USER:
        raise SystemExit("TARGET_GITHUB_USERNAME env var required")
    repos_report = gather_report(GITHUB_USER)
    html = render_report(repos_report)
    subject = f"GitHub Agent Report for {GITHUB_USER} - {time.strftime('%Y-%m-%d') }"
    if RECIPIENTS:
        send_sendgrid(subject, html, RECIPIENTS)
    else:
        print(html)


if __name__ == "__main__":
    main()
