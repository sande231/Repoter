# MainAgent MVP

This workspace contains a MainAgent reporting system with GitHub workflow monitoring, dashboard generation, and email reporting.

- `agent_sdk.py` — simple client used by subordinate agents to register and post telemetry.
- `ingestion_server.py` — Flask app that accepts registrations and telemetry and stores them in-memory.
- `main_agent.py` — collects metrics from the ingestion server, aggregates them, renders an HTML report, and sends email.
- `central_reporter.py` — queries GitHub repositories and workflow runs, renders an HTML report and dashboard, sends email, and optionally creates GitHub issues for failures.
- `templates/report_github.html` — Jinja2 template used for email reports.
- `templates/dashboard.html` — Jinja2 template used for the generated dashboard artifact.
- `tests/` — unit tests for the ingestion server, SDK, and GitHub reporter.
- `.github/workflows/daily_github_report.yml` — schedules daily reporter runs, runs tests, and uploads the dashboard artifact.
- `requirements.txt` — Python dependencies.

Quick start (requires Python 3.10+):

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run the reporter locally:

```bash
export TARGET_GITHUB_USERNAME=sande231
export RECIPIENTS=you@example.com
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@example.com
export SMTP_PASS=<app-password>
export TARGET_GH_PAT=<github-pat>
python central_reporter.py
```

3. Run tests:

```bash
pytest -q
```

Notes:
- `central_reporter.py` now generates `dashboard.html` and uploads it as a GitHub Actions artifact.
- Production systems should add monitoring, retries, persistence, secure secrets management, and issue deduplication.
