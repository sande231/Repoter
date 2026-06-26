"""Simple Agent SDK for reporting telemetry to the ingestion server.

This is an MVP helper library. In production, extend with batching,
retry/backoff, secure auth (mTLS/JWT), and richer metrics (psutil/OpenTelemetry).
"""
import os
import time
import uuid
import socket
import requests


class AgentSDK:
    def __init__(
        self,
        agent_id=None,
        ingestion_url=None,
        tags=None,
        api_key=None,
        api_key_header="X-API-KEY",
        retry_attempts=None,
        retry_backoff=None,
    ):
        self.agent_id = agent_id or f"agent-{uuid.uuid4()}"
        self.ingestion_url = ingestion_url or os.environ.get("INGESTION_URL", "http://localhost:5000")
        self.tags = tags or {}
        self.api_key = api_key or os.environ.get("INGESTION_API_KEY")
        self.api_key_header = api_key_header or os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
        self.retry_attempts = int(retry_attempts if retry_attempts is not None else os.environ.get("INGESTION_RETRY_ATTEMPTS", "5"))
        self.retry_backoff = float(retry_backoff if retry_backoff is not None else os.environ.get("INGESTION_RETRY_BACKOFF", "1.0"))

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers[self.api_key_header] = self.api_key
        return headers

    def _envelope(self, metrics: dict):
        return {
            "agent_id": self.agent_id,
            "timestamp": int(time.time()),
            "host": socket.gethostname(),
            "tags": self.tags,
            "metrics": metrics,
        }

    def _post_json(self, path: str, payload: dict) -> requests.Response:
        url = f"{self.ingestion_url.rstrip('/')}/{path.lstrip('/')}"
        headers = self._headers()
        attempts = max(1, self.retry_attempts)
        last_error = None

        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=5)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
                resp.raise_for_status()
            except requests.RequestException as exc:
                last_error = exc
                if attempt == attempts:
                    raise
                time.sleep(self.retry_backoff * attempt)

        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to POST {url}")

    def send_metrics(self, metrics: dict) -> requests.Response:
        payload = self._envelope(metrics)
        return self._post_json("/telemetry", payload)

    def report_problem(self, message: str, severity: str = "critical", details: dict = None) -> requests.Response:
        problem_payload = {
            "message": message,
            "severity": severity,
            "timestamp": int(time.time()),
        }
        if details:
            problem_payload["details"] = details

        metrics = {
            "status": "problem",
            "problem": problem_payload,
        }
        return self.send_metrics(metrics)

    def register(self, info: dict = None) -> requests.Response:
        payload = {"agent_id": self.agent_id, "info": info or {}, "tags": self.tags}
        return self._post_json("/register", payload)


if __name__ == "__main__":
    # quick demo
    sdk = AgentSDK()
    sdk.register({"version": "0.1-demo"})
    print("Registered", sdk.agent_id)
    sdk.send_metrics({"cpu_pct": 12.3, "mem_mb": 128})
    print("Sent sample metrics")
