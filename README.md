# Synapse MainAgent Reporter

Synapse MainAgent Reporter is a lightweight agent telemetry and reporting system. It collects health and activity signals from agents, stores recent telemetry through a Flask ingestion service, renders operational reports, and sends alerts through a durable email queue.

The current workspace also includes a Canvas Tutor adapter that can report Canvas connectivity and token health into the same MainAgent monitoring flow.

## What It Does

- Registers agents and records heartbeat telemetry.
- Aggregates agent metrics into an HTML performance report.
- Detects stale agents and reported agent problems.
- Attempts remediation through an agent `control_url` when available.
- Sends reports and alerts by email using a SQLite-backed queue.
- Monitors GitHub repositories and workflow runs through the central reporter.
- Supports a Canvas Tutor integration using either a student personal access token or Canvas OAuth when a Developer Key is available.

## Architecture

```text
Agent SDK clients
Canvas Tutor adapter
        |
        v
Flask ingestion server
        |
        v
MainAgent reporter ----> email queue ----> SMTP
        |
        v
HTML operations report

Central GitHub reporter ----> GitHub API ----> dashboard + email report
```

## Project Layout

| Path | Purpose |
| --- | --- |
| `agent_sdk.py` | Small client used by agents to register and publish telemetry. |
| `ingestion_server.py` | Flask API for `/register`, `/telemetry`, `/agents`, `/metrics`, and health checks. |
| `main_agent.py` | Collects ingestion data, detects problems, renders reports, and queues email. |
| `email_queue.py` | Durable SQLite email queue with retry behavior. |
| `central_reporter.py` | GitHub repository and workflow reporter with dashboard generation. |
| `canvas_tutor_adapter.py` | Canvas Tutor heartbeat publisher for MainAgent monitoring. |
| `canvas_oauth.py` | Optional local Canvas OAuth helper for institutions that provide Developer Keys. |
| `templates/` | Jinja2 templates for reports and dashboards. |
| `tests/` | Unit tests for ingestion, reporting, SDK, and Canvas helpers. |
| `docker-compose.yml` | Local multi-service deployment. |
| `RUNBOOK.md` | Operational startup and troubleshooting notes. |

## Quick Start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the test suite:

```bash
pytest -q
```

Create a local environment file:

```bash
cp .env.example .env
```

Do not commit `.env`. It is intentionally ignored because it can contain Canvas tokens, SMTP passwords, and GitHub credentials.

## Local Docker Run

Start the ingestion server and Canvas Tutor adapter:

```bash
docker compose up --build ingestion canvas_tutor_adapter
```

Start the full local stack:

```bash
docker compose up --build ingestion main_agent email_worker canvas_tutor_adapter
```

Useful local checks:

```bash
curl -H "X-API-KEY: change-me" http://localhost:5000/health
curl -H "X-API-KEY: change-me" http://localhost:5000/agents
curl -H "X-API-KEY: change-me" http://localhost:5000/metrics/canvas-tutor-agent
```

## Configuration

Most settings are loaded from environment variables. Use `.env.example` as the template for local development.

| Variable | Purpose |
| --- | --- |
| `INGESTION_URL` | Base URL for the ingestion server. |
| `INGESTION_API_KEY` | Optional API key required by ingestion endpoints. |
| `INGESTION_API_KEY_HEADER` | Header name used for the ingestion API key. |
| `RECIPIENTS` | Comma-separated report recipients. |
| `SMTP_HOST`, `SMTP_PORT` | SMTP server configuration. |
| `SMTP_USER`, `SMTP_PASS` | SMTP login. For Gmail, use a Google App Password. |
| `TARGET_GITHUB_USERNAME` | GitHub account monitored by the central reporter. |
| `TARGET_GH_PAT` | GitHub token for repository and workflow access. |
| `REPORT_INTERVAL_SECONDS` | Enables scheduled MainAgent reports when set. |
| `HEARTBEAT_THRESHOLD_SECONDS` | Age threshold for stale-agent alerts. |

## Canvas Tutor Integration

For a student-owned private setup, the simplest path is a Canvas personal access token:

```env
CANVAS_BASE_URL=https://your-school.instructure.com
CANVAS_ACCESS_TOKEN=your_canvas_access_token
CANVAS_TUTOR_VERIFY_API=true
```

When `CANVAS_TUTOR_VERIFY_API=true`, the adapter calls Canvas with the token and reports whether authentication is healthy. MainAgent does not store or generate Canvas tokens; it monitors the adapter and alerts when Canvas auth becomes unhealthy.

If your institution provides a Canvas Developer Key, the optional OAuth helper can generate and refresh a token:

```bash
docker compose up --build canvas_oauth
```

Then open:

```text
http://localhost:8080/canvas/oauth/start
```

The OAuth callback stores tokens under `.canvas_tokens/`, which is ignored by git.

## GitHub Reporter

Run the GitHub workflow reporter locally after setting GitHub and SMTP configuration:

```bash
python central_reporter.py
```

The reporter can:

- Scan repositories for recent workflow runs.
- Render an HTML report and dashboard.
- Send email summaries.
- Optionally create GitHub issues for failures.

## Security Notes

- Never commit `.env`, `.canvas_tokens/`, access tokens, SMTP passwords, or app passwords.
- Use `INGESTION_API_KEY` when exposing ingestion beyond local development.
- Canvas personal tokens are best for private/student workflows. Multi-user Canvas apps should use OAuth with an institution-approved Developer Key.
- Gmail SMTP requires an App Password when 2-Step Verification is enabled.

## Development

Run tests before pushing changes:

```bash
pytest -q
```

Validate Docker Compose configuration:

```bash
docker compose config
```

For operational steps and troubleshooting, see `RUNBOOK.md`.
