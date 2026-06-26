# Synapse MainAgent Reporter

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Ingestion_API-000000?style=for-the-badge&logo=flask&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Workflow_Reports-2088FF?style=for-the-badge&logo=githubactions&logoColor=white)
![Author](https://img.shields.io/badge/Author-Sandeep-0B5FFF?style=for-the-badge)

Synapse MainAgent Reporter is a Python reporting system that watches agent health, GitHub repositories, GitHub Actions workflow runs, and email delivery. It collects operational data, turns it into readable reports, and sends alerts when something needs attention.

The goal is simple: one place to understand what is happening across your agents and GitHub repositories.

## What The Reporter Does

The project has two reporting paths:

1. **MainAgent reporter**
   - Receives telemetry from agents.
   - Tracks agent health, heartbeats, failures, and problem reports.
   - Detects stale or unhealthy agents.
   - Attempts remediation when an agent provides a `control_url`.
   - Sends agent performance reports by email.

2. **GitHub workflow reporter**
   - Checks repositories under the configured GitHub account.
   - Reads recent GitHub Actions workflow runs.
   - Detects failed, cancelled, or unstable workflows.
   - Builds an HTML report and dashboard.
   - Can email a summary and optionally create GitHub issues for failures.

Canvas Tutor is only one optional agent adapter. The reporter is not just for Canvas; it is designed to monitor multiple agents and GitHub repositories.

## System Overview

```text
Subordinate agents / adapters
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
   email_queue.py  ->  SMTP report email

central_reporter.py  ->  GitHub repositories + workflow runs
                    ->  HTML report + dashboard + email
```

## Main Components

| Component | What it does |
| --- | --- |
| `main_agent.py` | Main agent reporter for agent health, stale-agent detection, problem alerts, and report generation. |
| `central_reporter.py` | GitHub repository and workflow reporter. It checks repositories, workflow runs, failures, and dashboard data. |
| `ingestion_server.py` | Flask ingestion API where agents register and send telemetry. |
| `agent_sdk.py` | Simple SDK that other agents use to send telemetry to ingestion. |
| `email_queue.py` | SQLite-backed email queue with retry support. |
| `email_worker.py` | Worker process that sends queued emails. |
| `canvas_tutor_adapter.py` | Optional Canvas Tutor adapter that reports Canvas connection/token health as one monitored agent. |
| `canvas_oauth.py` | Optional Canvas OAuth helper for schools that provide a Canvas Developer Key. |
| `templates/` | HTML templates for email reports and dashboards. |
| `.github/workflows/` | GitHub Actions workflows for scheduled reporting and Python checks. |
| `docker-compose.yml` | Local Docker setup for ingestion, reporting, email worker, and optional adapters. |
| `RUNBOOK.md` | Operational startup and troubleshooting notes. |

## Key Features

- Agent registration and telemetry ingestion.
- Heartbeat monitoring for multiple agents.
- Stale-agent and unhealthy-agent alerts.
- Optional agent self-healing through `control_url`.
- GitHub repository discovery and workflow monitoring.
- HTML reports and dashboard generation.
- Email delivery through SMTP.
- Durable SQLite email queue with retry behavior.
- Docker Compose local deployment.
- Unit tests for the core reporting and ingestion logic.
- Optional Canvas Tutor monitoring adapter.

## Quick Start

Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

Edit `.env` with your local values. Do not commit `.env`; it can contain tokens, passwords, and private API keys.

Run tests:

```bash
pytest -q
```

## Run With Docker

Start the core reporting stack:

```bash
docker compose up --build ingestion main_agent email_worker
```

Start ingestion with the optional Canvas Tutor adapter:

```bash
docker compose up --build ingestion canvas_tutor_adapter
```

Check ingestion health:

```bash
curl -H "X-API-KEY: change-me" http://localhost:5000/health
curl -H "X-API-KEY: change-me" http://localhost:5000/agents
```

## GitHub Workflow Reporting

Configure these values in `.env`:

```env
TARGET_GITHUB_USERNAME=sande231
TARGET_GH_PAT=your_github_token
RECIPIENTS=you@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_google_app_password
```

Run the GitHub reporter:

```bash
python central_reporter.py
```

The report shows repository activity, workflow status, failed runs, and dashboard output for the configured GitHub account.

## Agent Reporting

Agents send data through `agent_sdk.py` into the ingestion server. MainAgent reads that data and builds a report.

Typical agent telemetry includes:

- Agent ID and tags.
- Last heartbeat time.
- Health status.
- Completed and failed task counts.
- Latency or custom metrics.
- Problem messages.
- Optional remediation endpoint.

The ingestion API exposes:

```text
GET  /health
GET  /agents
GET  /metrics/<agent_id>
POST /register
POST /telemetry
```

## Optional Canvas Tutor Adapter

Canvas Tutor is included as an example of one monitored agent. For a student/private setup, use a Canvas personal access token:

```env
CANVAS_BASE_URL=https://your-school.instructure.com
CANVAS_ACCESS_TOKEN=your_canvas_access_token
CANVAS_TUTOR_VERIFY_API=true
```

If the token works, the adapter reports healthy telemetry to MainAgent. If the token expires or is revoked, MainAgent can report that Canvas Tutor is degraded.

If a school provides a Canvas Developer Key, the optional OAuth helper can be started with:

```bash
docker compose up --build canvas_oauth
```

Then open:

```text
http://localhost:8080/canvas/oauth/start
```

## Security

- Never commit `.env`.
- Never commit Canvas tokens, GitHub tokens, SMTP passwords, or Gmail app passwords.
- `.env` and `.canvas_tokens/` are ignored by git.
- Use `INGESTION_API_KEY` when exposing ingestion outside local development.
- Store production secrets in GitHub Actions secrets or a real secret manager.

## Development Checks

Run tests:

```bash
pytest -q
```

Validate Docker Compose:

```bash
docker compose config --quiet
```

## Project Status

This is an MVP reporting and monitoring system. It is ready for local development and incremental integrations. See `RUNBOOK.md` for operations and `TODO.md` for planned improvements.

## Author

Built by **Sandeep**.
