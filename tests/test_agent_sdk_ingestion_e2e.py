import socket
import sys
import threading
import time
import os

import pytest
import requests
from werkzeug.serving import make_server

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ingestion_server
from agent_sdk import AgentSDK
from ingestion_server import AGENTS, METRICS, app
from local_queue import LocalQueue


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class IngestionServerThread:
    def __init__(self, port):
        self.port = port
        self.server = make_server("127.0.0.1", port, app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"http://127.0.0.1:{self.port}/health",
                    timeout=0.5,
                )
                if response.status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.05)
        raise RuntimeError("ingestion server did not start")

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)


@pytest.fixture
def clean_ingestion(monkeypatch):
    monkeypatch.setattr(ingestion_server, "INGESTION_API_KEY", None)
    AGENTS.clear()
    METRICS.clear()
    yield
    AGENTS.clear()
    METRICS.clear()


def test_agent_sdk_replays_local_queue_to_ingestion_server(clean_ingestion, tmp_path):
    port = _free_port()
    ingestion_url = f"http://127.0.0.1:{port}"
    queue = LocalQueue(str(tmp_path / "telemetry.db"))
    sdk = AgentSDK(
        agent_id="canvas-tutor-agent",
        ingestion_url=ingestion_url,
        retry_attempts=1,
        retry_backoff=0,
        local_queue=queue,
        tags={"agent_type": "canvas_tutor"},
    )

    failed_result = sdk.send_metrics({"status": "QUEUED_WHILE_DOWN"})

    assert failed_result is None
    assert queue.get_pending_count() == 1

    server = IngestionServerThread(port)
    try:
        server.start()

        register_response = sdk.register({"name": "Canvas Tutor"})
        live_response = sdk.send_metrics({"status": "HEALTHY"})

        assert register_response.status_code == 200
        assert live_response.status_code == 200
        assert queue.get_pending_count() == 0
        assert "canvas-tutor-agent" in AGENTS

        statuses = [
            entry["metrics"]["status"]
            for entry in METRICS["canvas-tutor-agent"]
        ]
        assert statuses == ["HEALTHY", "QUEUED_WHILE_DOWN"]
    finally:
        server.stop()
