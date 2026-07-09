import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from canvas_tutor_adapter import CanvasTutorAdapter


def test_publish_heartbeat_sends_healthy_telemetry(monkeypatch):
    sent_metrics = []

    class FakeSDK:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.last_error = None

        def send_metrics(self, metrics):
            sent_metrics.append(metrics)
            return object()

    monkeypatch.setattr("canvas_tutor_adapter.AgentSDK", FakeSDK)
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TUTOR_LATENCY_MS", "123")
    monkeypatch.setenv("CANVAS_TUTOR_SESSION_COUNT", "4")

    adapter = CanvasTutorAdapter()
    adapter.publish_heartbeat()

    assert sent_metrics == [
        {
            "status": "HEALTHY",
            "latency_ms": 123,
            "tasks_completed": 1,
            "tasks_failed": 0,
            "last_event": sent_metrics[0]["last_event"],
            "session_count": 4,
            "canvas_oauth": "not_configured",
        }
    ]


def test_publish_heartbeat_degrades_when_canvas_token_missing(monkeypatch, tmp_path):
    sent_metrics = []

    class FakeSDK:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.last_error = None

        def send_metrics(self, metrics):
            sent_metrics.append(metrics)
            return object()

    monkeypatch.setattr("canvas_tutor_adapter.AgentSDK", FakeSDK)
    monkeypatch.setenv("CANVAS_BASE_URL", "https://school.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN_STORE", str(tmp_path / "missing_token.json"))

    adapter = CanvasTutorAdapter()
    adapter.publish_heartbeat()

    assert sent_metrics[0]["status"] == "DEGRADED"
    assert sent_metrics[0]["tasks_failed"] == 1
    assert sent_metrics[0]["canvas_oauth"] == "error"
    assert "No Canvas OAuth token found" in sent_metrics[0]["canvas_error"]
