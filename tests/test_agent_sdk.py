import sys
import os
import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent_sdk import AgentSDK


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
