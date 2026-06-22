# Synapse Runbook

## Overview

This repository contains a small agent telemetry platform and a GitHub workflow reporter.

- `ingestion_server.py` — receives agent registration and telemetry.
- `main_agent.py` — collects metrics from the ingestion server and enqueues email reports.
- `email_queue.py` — durable SQLite-backed queue and worker for sending emails.
- `central_reporter.py` — GitHub repo/workflow reporter, dashboard generator, and optional issue creator.

## Startup

### Local development

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in secrets.

3. Start the ingestion server:

```bash
python ingestion_server.py
```

4. Start the email worker in another shell:

```bash
python -c "from email_queue import worker_loop; worker_loop()"
```

5. Run the main agent once or with scheduling:

```bash
export REPORT_INTERVAL_SECONDS=60
python main_agent.py
```

### Docker compose

```bash
docker compose up --build
```

This will start:
- `ingestion` (Flask server)
- `main_agent` (runs once or with scheduling)
- `email_worker` (durable email queue worker)

## Observability

### Ingestion server

- Health check: `GET /health`
- Metrics: `GET /metrics`

### Logging

- Enable debug logging by setting `LOG_LEVEL=DEBUG`.
- All services use structured logs with timestamps.

## Reliability

- `email_queue.py` persists outgoing email tasks in SQLite.
- `main_agent.py` uses retry/backoff when calling the ingestion server.
- `central_reporter.py` uses retries for GitHub API requests and SMTP.

## Security

- `ingestion_server.py` supports API key authentication via `INGESTION_API_KEY`.
- Protect secrets in GitHub Actions using repository secrets only.
- Use minimal-scope GitHub PAT for `TARGET_GH_PAT`.

## Troubleshooting

### Ingestion returns 401

Ensure the request includes the correct header:

```bash
curl -H "X-API-KEY: <your-key>" http://localhost:5000/agents
```

### Email worker is not sending

- Confirm `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS` are set.
- Check `email_queue.db` for pending rows.
- Tail logs in the worker shell.

### GitHub reporter failure

- Check the GitHub Actions logs.
- Ensure `TARGET_GH_PAT` has repo permissions for issues and workflows.
- Confirm `GMAIL_SMTP_PASS` is valid for sending email.
