"""Demo script that runs the ingestion server in-process, posts sample telemetry,
and invokes the MainAgent to generate a report.

Usage: python run_mvp.py
"""
import threading
import time
import random
from ingestion_server import app, METRICS, AGENTS
from agent_sdk import AgentSDK
from main_agent import run_once


def start_ingestion():
    # start Flask app in a background thread
    def _run():
        app.run(port=5000)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # give server time to start
    time.sleep(1)


def send_sample(agent_id, count=5, interval=0.2):
    sdk = AgentSDK(agent_id=agent_id, ingestion_url="http://localhost:5000")
    sdk.register({"version": "mvp-0.1"})
    for i in range(count):
        metrics = {
            "cpu_pct": round(random.uniform(1, 60), 2),
            "mem_mb": round(random.uniform(50, 512), 1),
            "tasks_processed": random.randint(0, 50),
        }
        sdk.send_metrics(metrics)
        time.sleep(interval)


if __name__ == "__main__":
    print("Starting ingestion server...")
    start_ingestion()
    print("Sending sample telemetry from two agents...")
    send_sample("agent-demo-1", count=10)
    send_sample("agent-demo-2", count=8)
    print("Invoking MainAgent to generate report (prints since no SMTP configured)...")
    run_once(window_seconds=3600)
