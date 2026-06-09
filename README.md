# MainAgent MVP

This workspace contains a minimal MVP of a MainAgent reporting system:

- `agent_sdk.py` — simple client used by subordinate agents to register and post telemetry.
- `ingestion_server.py` — Flask app that accepts registrations and telemetry and stores them in-memory.
- `main_agent.py` — collects metrics from the ingestion server, aggregates them, renders an HTML report, and sends email (prints to stdout if SMTP not configured).
- `templates/report.html` — Jinja2 template used for HTML reports.
- `run_mvp.py` — demo runner that starts the ingestion server, sends sample telemetry, and runs the reporter.
- `requirements.txt` — Python dependencies.

Quick start (requires Python 3.8+):

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run the demo:

```bash
python run_mvp.py
```

You should see the generated HTML report printed to stdout (SMTP not configured by default).

To enable SMTP email delivery, set environment variables before running `main_agent.py` or `run_mvp.py`:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=you@example.com
export SMTP_PASS=secret
export RECIPIENTS=ops@example.com,dev@example.com
python main_agent.py
```

Notes:
- This is an MVP focused on demonstrating end-to-end flow. Production systems should add persistence (TSDB), authentication (mTLS/OAuth2), message broker for durability, robust retries, batching, rate limits, and secure secrets management.
