# Synapse MainAgent Reporter

**Author:** Sandeep

**Repository:** `sande231/Repoter`

Synapse MainAgent Reporter is a Python workspace for monitoring agents, checking GitHub workflow activity, generating operational reports, and sending email alerts. It is designed as a simple reporting hub: different agents and services send status information into one place, and MainAgent turns that information into readable reports.

## Simple Overview

This project has five main parts:

1. **Ingestion server** - receives agent registration and telemetry.
2. **MainAgent reporter** - reads telemetry, detects stale or unhealthy agents, and prepares reports.
3. **GitHub reporter** - checks repositories and workflow runs, then builds a GitHub activity report.
4. **Email queue** - stores outgoing emails in SQLite and retries failed sends.
5. **Agent SDK and adapters** - help other agents send data into the system.

Canvas Tutor is included as one optional adapter. It is not the whole project; it is just one agent that can report Canvas token and connection health into MainAgent.

## How It Works

```text
Agents / adapters
      |
      v
agent_sdk.py
      |
      v
ingestion_server.py
      |
      v
main_agent.py
      |
      v
email_queue.py -> SMTP email report

central_reporter.py -> GitHub API -> HTML report + dashboard
```

## Main Files

| File or Folder | Simple Explanation |
| --- | --- |
| `agent_sdk.py` | Small helper used by agents to register and send telemetry. |
| `ingestion_server.py` | Flask server that receives agent data and exposes health/metrics endpoints. |
| `main_agent.py` | Main reporter that reads agent data, finds problems, and queues email reports. |
| `central_reporter.py` | GitHub reporter that checks repositories, workflow runs, failures, and dashboard data. |
| `email_queue.py` | Durable email queue backed by SQLite. |
| `email_worker.py` | Worker entry point for sending queued email. |
| `canvas_tutor_adapter.py` | Optional Canvas Tutor adapter for reporting Canvas connection health. |
| `canvas_oauth.py` | Optional Canvas OAuth helper for schools that provide a Canvas Developer Key. |
| `synapse_agent.py` | Larger Synapse agent/orchestrator example used for agent workflows. |
| `templates/` | HTML templates for email reports and dashboards. |
| `tests/` | Automated tests for the main project modules. |
| `.github/workflows/` | GitHub Actions workflows for scheduled reports and Python checks. |
| `docker-compose.yml` | Local multi-service Docker setup. |
| `RUNBOOK.md` | Operational notes for startup and troubleshooting. |

## Features

- Agent registration and heartbeat telemetry.
- Agent problem detection and stale-agent alerts.
- Optional self-healing calls through an agent `control_url`.
- GitHub repository and workflow monitoring.
- HTML report and dashboard generation.
- Email delivery through SMTP.
- Durable email retry queue using SQLite.
- Docker Compose setup for local services.
- Canvas Tutor monitoring as an optional adapter.
- Unit tests for core behavior.

## Quick Start

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

Edit `.env` with your local values. Do not commit `.env`; it can contain tokens and passwords.

Run tests:

```bash
pytest -q
```

## Running Locally with Docker

Start the core telemetry stack:

```bash
docker compose up --build ingestion main_agent email_worker
```

Start the Canvas Tutor adapter with ingestion:

```bash
docker compose up --build ingestion canvas_tutor_adapter
```

Check the ingestion API:

```bash
curl -H "X-API-KEY: change-me" http://localhost:5000/health
curl -H "X-API-KEY: change-me" http://localhost:5000/agents
```

## Email Setup

MainAgent uses these environment variables for email:

```env
RECIPIENTS=you@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_google_app_password
```

For Gmail, `SMTP_PASS` must be a Google App Password, not your normal Gmail password.

## GitHub Reporter Setup

The GitHub reporter uses:

```env
TARGET_GITHUB_USERNAME=sande231
TARGET_GH_PAT=your_github_token
```

Run it locally with:

```bash
python central_reporter.py
```

The reporter can summarize repositories, workflow runs, failures, and dashboard output.

## Canvas Tutor Setup

Canvas Tutor is optional. For a student/private setup, use a personal Canvas access token:

```env
CANVAS_BASE_URL=https://your-school.instructure.com
CANVAS_ACCESS_TOKEN=your_canvas_access_token
CANVAS_TUTOR_VERIFY_API=true
```

If the token is valid, the adapter reports `HEALTHY` telemetry to MainAgent. If the token expires or is revoked, MainAgent can report that Canvas Tutor is degraded.

If your school provides a Canvas Developer Key, you can also use the OAuth helper:

```bash
docker compose up --build canvas_oauth
```

Then open:

```text
http://localhost:8080/canvas/oauth/start
```

OAuth tokens are stored under `.canvas_tokens/`, which is ignored by git.

## Important Security Notes

- Never commit `.env`.
- Never commit Canvas access tokens, GitHub tokens, SMTP passwords, or Gmail app passwords.
- `.canvas_tokens/` and `.env` are ignored by git.
- Use `INGESTION_API_KEY` when exposing the ingestion server outside local development.
- Use repository secrets for GitHub Actions secrets, not plain text files.

## Development Checks

Run the test suite:

```bash
pytest -q
```

Validate Docker Compose:

```bash
docker compose config --quiet
```

## Status

This project is an MVP reporting and monitoring system. It is ready for local development, testing, and incremental integrations. See `TODO.md` and `RUNBOOK.md` for planned work and operational guidance.
