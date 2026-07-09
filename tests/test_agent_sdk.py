import sys
import os
import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent_sdk import AgentSDK, send_with_retry
from local_queue import LocalQueue


def test_envelope_structure():
    sdk = AgentSDK(agent_id="test-agent", ingestion_url="http://example.local")
    env = sdk._envelope({"cpu": 1.2})
    assert env["agent_id"] == "test-agent"
    assert "timestamp" in env
    assert env["metrics"]["cpu"] == 1.2


def test_headers_include_api_key():
    sdk = AgentSDK(agent_id="test-agent", ingestion_url="http://example.local", api_key="secret", api_key_header="X-INGESTION-KEY")
    headers = sdk._headers()
    assert headers["Content-Type"] == "application/json"
    assert headers["X-INGESTION-KEY"] == "secret"


def test_post_json_retries_transient_connection_error(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_post(url, json, headers, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise requests.ConnectionError("ingestion not ready")
        return FakeResponse()

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda _: None)

    sdk = AgentSDK(
        agent_id="test-agent",
        ingestion_url="http://example.local",
        retry_attempts=2,
        retry_backoff=0,
    )
    resp = sdk.register({"version": "test"})

    assert resp.status_code == 200
    assert calls == ["http://example.local/register", "http://example.local/register"]


def test_send_metrics_queues_when_ingestion_unreachable(monkeypatch, tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"))

    def fake_post(url, json, headers, timeout):
        raise requests.ConnectionError("ingestion unreachable")

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda _: None)

    sdk = AgentSDK(
        agent_id="test-agent",
        ingestion_url="http://example.local",
        retry_attempts=1,
        retry_backoff=0,
        local_queue=queue,
    )

    result = sdk.send_metrics({"cpu": 1.2})

    assert result is None
    assert "ingestion unreachable" in sdk.last_error
    queued = queue.dequeue()
    assert queued["agent_id"] == "test-agent"
    assert queued["metrics"] == {"cpu": 1.2}


def test_send_metrics_flushes_existing_local_queue_after_success(monkeypatch, tmp_path):
    queue = LocalQueue(str(tmp_path / "telemetry.db"))
    queued_payload = {
        "agent_id": "test-agent",
        "timestamp": 1,
        "host": "test-host",
        "tags": {},
        "metrics": {"status": "queued"},
    }
    queue.enqueue(queued_payload)
    sent_payloads = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_post(url, json, headers, timeout):
        sent_payloads.append(json)
        return FakeResponse()

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)

    sdk = AgentSDK(
        agent_id="test-agent",
        ingestion_url="http://example.local",
        retry_attempts=1,
        retry_backoff=0,
        local_queue=queue,
    )

    result = sdk.send_metrics({"status": "live"})

    assert result.status_code == 200
    assert queue.get_pending_count() == 0
    assert sent_payloads[0]["metrics"] == {"status": "live"}
    assert sent_payloads[1] == queued_payload


def test_register_degrades_gracefully_when_ingestion_unreachable(monkeypatch):
    def fake_post(url, json, headers, timeout):
        raise requests.ConnectionError("ingestion unreachable")

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda _: None)

    sdk = AgentSDK(
        agent_id="test-agent",
        ingestion_url="http://example.local",
        retry_attempts=1,
        retry_backoff=0,
    )

    result = sdk.register({"version": "test"})

    assert result is None
    assert "ingestion unreachable" in sdk.last_error


def test_post_json_uses_exponential_backoff_with_jitter(monkeypatch):
    calls = []
    sleeps = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_post(url, json, headers, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise requests.ConnectionError("not ready")
        return FakeResponse()

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.random.uniform", lambda low, high: high / 2)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda delay: sleeps.append(delay))

    sdk = AgentSDK(
        agent_id="test-agent",
        ingestion_url="http://example.local",
        retry_attempts=3,
        retry_backoff=2,
    )

    resp = sdk.register({"version": "test"})

    assert resp.status_code == 200
    assert calls == ["http://example.local/register"] * 3
    assert sleeps == [1.0, 2.0]


def test_send_with_retry_succeeds_without_retry(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return FakeResponse()

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)

    success, retries, error = send_with_retry("http://example.local/telemetry", {"ok": True})

    assert success is True
    assert retries == 0
    assert error == ""
    assert calls == [("http://example.local/telemetry", {"ok": True}, 5)]


def test_send_with_retry_uses_exponential_backoff_with_jitter(monkeypatch):
    calls = []
    sleeps = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise requests.ConnectionError("not ready")
        return FakeResponse()

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.random.uniform", lambda low, high: high / 2)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda delay: sleeps.append(delay))

    success, retries, error = send_with_retry(
        "http://example.local/telemetry",
        {"ok": True},
        max_retries=3,
        base_delay=1,
    )

    assert success is True
    assert retries == 2
    assert error == ""
    assert calls == ["http://example.local/telemetry"] * 3
    assert sleeps == [0.5, 1.0]


def test_send_with_retry_returns_last_error_after_exhaustion(monkeypatch):
    calls = []
    sleeps = []

    def fake_post(url, json, timeout):
        calls.append(url)
        raise requests.Timeout("timed out")

    monkeypatch.setattr("agent_sdk.requests.post", fake_post)
    monkeypatch.setattr("agent_sdk.random.uniform", lambda low, high: high)
    monkeypatch.setattr("agent_sdk.time.sleep", lambda delay: sleeps.append(delay))

    success, retries, error = send_with_retry(
        "http://example.local/telemetry",
        {"ok": True},
        max_retries=2,
        base_delay=2,
    )

    assert success is False
    assert retries == 2
    assert "timed out" in error
    assert calls == ["http://example.local/telemetry"] * 3
    assert sleeps == [2.0, 4.0]
