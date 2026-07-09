"""Simple Agent SDK for reporting telemetry to the ingestion server.

This is an MVP helper library. In production, extend with batching,
retry/backoff, secure auth (mTLS/JWT), and richer metrics (psutil/OpenTelemetry).
"""
import os
import random
import time
import uuid
import socket
import requests

from local_queue import LocalQueue


def send_with_retry(url, data, max_retries=5, base_delay=1):
    """Send telemetry with exponential backoff and jitter.

    Returns:
        tuple: (success, retries_attempted, error_message)
    """
    max_retries = max(0, int(max_retries))
    base_delay = max(0, float(base_delay))
    last_error = ""

    for retries_attempted in range(max_retries + 1):
        try:
            resp = requests.post(url, json=data, timeout=5)
            resp.raise_for_status()
            return True, retries_attempted, ""
        except requests.RequestException as exc:
            last_error = str(exc)
            if retries_attempted == max_retries:
                return False, retries_attempted, last_error
            delay_cap = base_delay * (2 ** retries_attempted)
            time.sleep(random.uniform(0, delay_cap))

    return False, max_retries, last_error


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
        local_queue=None,
    ):
        self.agent_id = agent_id or f"agent-{uuid.uuid4()}"
        self.ingestion_url = ingestion_url or os.environ.get("INGESTION_URL", "http://localhost:5000")
        self.tags = tags or {}
        self.api_key = api_key or os.environ.get("INGESTION_API_KEY")
        self.api_key_header = api_key_header or os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
        self.retry_attempts = int(retry_attempts if retry_attempts is not None else os.environ.get("INGESTION_RETRY_ATTEMPTS", "5"))
        self.retry_backoff = float(retry_backoff if retry_backoff is not None else os.environ.get("INGESTION_RETRY_BACKOFF", "1.0"))
        queue_db = os.environ.get("LOCAL_QUEUE_DB")
        self.local_queue = local_queue or (LocalQueue(queue_db) if queue_db else None)
        self.last_error = None

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
                delay_cap = self.retry_backoff * (2 ** (attempt - 1))
                time.sleep(random.uniform(0, delay_cap))

        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to POST {url}")

    def send_metrics(self, metrics: dict) -> requests.Response | None:
        payload = self._envelope(metrics)
        try:
            resp = self._post_json("/telemetry", payload)
            self.last_error = None
            self.flush_local_queue()
            return resp
        except requests.RequestException as exc:
            self.last_error = str(exc)
            if self.local_queue:
                try:
                    self.local_queue.enqueue(payload)
                except Exception as queue_exc:
                    self.last_error = f"{self.last_error}; local queue failed: {queue_exc}"
            return None

    def flush_local_queue(self, max_items: int | None = None) -> dict:
        """Replay queued telemetry through ingestion and return a summary."""
        result = {
            "attempted": 0,
            "sent": 0,
            "failed": 0,
            "pending": 0,
            "error": "",
        }
        if not self.local_queue:
            return result

        try:
            while max_items is None or result["attempted"] < max_items:
                if self.local_queue.get_pending_count() == 0:
                    break

                payload = self.local_queue.dequeue()
                if payload is None:
                    break

                result["attempted"] += 1
                try:
                    self._post_json("/telemetry", payload)
                except requests.RequestException as exc:
                    result["failed"] += 1
                    self.last_error = str(exc)
                    result["error"] = self.last_error
                    self.local_queue.requeue_sent()
                    break

                self.local_queue.clear_sent()
                result["sent"] += 1

            result["pending"] = self.local_queue.get_pending_count()
            if result["failed"] == 0 and result["sent"] > 0:
                self.last_error = None
            return result
        except Exception as exc:
            self.last_error = f"local queue replay failed: {exc}"
            result["error"] = self.last_error
            try:
                result["pending"] = self.local_queue.get_pending_count()
            except Exception:
                result["pending"] = -1
            return result

    def report_problem(self, message: str, severity: str = "critical", details: dict = None) -> requests.Response | None:
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

    def register(self, info: dict = None) -> requests.Response | None:
        payload = {"agent_id": self.agent_id, "info": info or {}, "tags": self.tags}
        try:
            resp = self._post_json("/register", payload)
            self.last_error = None
            return resp
        except requests.RequestException as exc:
            self.last_error = str(exc)
            return None


if __name__ == "__main__":
    # quick demo
    sdk = AgentSDK()
    sdk.register({"version": "0.1-demo"})
    print("Registered", sdk.agent_id)
    sdk.send_metrics({"cpu_pct": 12.3, "mem_mb": 128})
    print("Sent sample metrics")
