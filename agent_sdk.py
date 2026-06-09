"""Simple Agent SDK for reporting telemetry to the ingestion server.

This is an MVP helper library. In production, extend with batching,
retry/backoff, secure auth (mTLS/JWT), and richer metrics (psutil/OpenTelemetry).
"""
import os
import time
import uuid
import socket
import json
import requests


class AgentSDK:
    def __init__(self, agent_id=None, ingestion_url=None, tags=None):
        self.agent_id = agent_id or f"agent-{uuid.uuid4()}"
        self.ingestion_url = ingestion_url or os.environ.get("INGESTION_URL", "http://localhost:5000")
        self.tags = tags or {}

    def _envelope(self, metrics: dict):
        return {
            "agent_id": self.agent_id,
            "timestamp": int(time.time()),
            "host": socket.gethostname(),
            "tags": self.tags,
            "metrics": metrics,
        }

    def send_metrics(self, metrics: dict) -> requests.Response:
        url = f"{self.ingestion_url.rstrip('/')}/telemetry"
        payload = self._envelope(metrics)
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        return resp

    def register(self, info: dict = None) -> requests.Response:
        url = f"{self.ingestion_url.rstrip('/')}/register"
        payload = {"agent_id": self.agent_id, "info": info or {}, "tags": self.tags}
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        return resp


if __name__ == "__main__":
    # quick demo
    sdk = AgentSDK()
    sdk.register({"version": "0.1-demo"})
    print("Registered", sdk.agent_id)
    sdk.send_metrics({"cpu_pct": 12.3, "mem_mb": 128})
    print("Sent sample metrics")
