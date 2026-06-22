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

Optional configuration:

```bash
export REPO_INCLUDE=my-repo,other-repo
export REPO_EXCLUDE=archive-repo
export MAX_REPOS=20
export LOG_LEVEL=INFO
export RETRY_ATTEMPTS=3
export RETRY_BACKOFF=2.0
export SMTP_RETRY_ATTEMPTS=2
export SMTP_RETRY_BACKOFF=2.0
export AUTO_CREATE_ISSUES=true
export ISSUE_LABEL=agent-report
```

3. Run tests:

```bash
pytest -q
```

## Deployment

### Docker compose

A containerized deployment is available via `docker-compose.yml`:

```bash
docker compose up --build
```

The compose setup includes:
- `ingestion` — Flask telemetry ingestion server
- `main_agent` — periodic report collector and enqueuer
- `email_worker` — durable email queue worker

### Environment configuration

Use `.env.example` as a starting point for local or containerized deployments.

## Observability

- Ingestion health: `GET /health`
- Ingestion metrics: `GET /metrics`
- Enable verbose logs with `LOG_LEVEL=DEBUG`

## Security & reliability

- `ingestion_server.py` can require `INGESTION_API_KEY` for API access.
- `email_queue.py` persists outgoing emails in SQLite and retries failed sends.
- `main_agent.py` retries ingestion API calls with backoff.
- `central_reporter.py` retries GitHub API and SMTP, and skips duplicate issue creation.

## Runbook

See `RUNBOOK.md` for startup steps, troubleshooting, and operational guidance.
