import json
import logging
import os
import sys

from flask import Flask
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_config import AgentConfig, ConfigManager
from config_service_api import ConfigServiceAPI, create_config_service_app
from structured_logger import StructuredLogger


class FakeClock:
    def __init__(self, current=1000.0):
        self.current = current

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


class CapturingStructuredLogger:
    def __init__(self):
        self.events = []
        self.errors = []

    def log_event(self, event_name, **kwargs):
        self.events.append({"event_name": event_name, **kwargs})

    def log_error(self, error, context):
        self.errors.append({"error": str(error), "context": context})


def _service(tmp_path, registered_agents=None, config_data=None, clock=None):
    config_file = tmp_path / "agent-config.json"
    if config_data is not None:
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
    manager = ConfigManager(
        config_file=config_file,
        cache_ttl_seconds=300,
        clock=(clock.now if clock else None),
    )
    logger = CapturingStructuredLogger()
    service = ConfigServiceAPI(
        app=Flask(__name__),
        config_manager=manager,
        registered_agents=registered_agents or {"agent-1"},
        structured_logger=logger,
        clock=(clock.now if clock else None),
    )
    return service, logger, config_file


def test_get_config_returns_registered_agent_config(tmp_path):
    service, logger, _config_file = _service(
        tmp_path,
        config_data={"telemetry_batch_size": 75},
    )
    client = service.app.test_client()

    response = client.get("/config/agent-1")

    assert response.status_code == 200
    assert response.get_json()["telemetry_batch_size"] == 75
    assert logger.events[0]["event_name"] == "config_get_requested"


def test_get_config_returns_404_for_unregistered_agent(tmp_path):
    service, _logger, _config_file = _service(tmp_path, registered_agents={"agent-1"})
    client = service.app.test_client()

    response = client.get("/config/missing-agent")

    assert response.status_code == 404
    assert response.get_json() == {"error": "agent not registered"}


def test_post_config_updates_valid_config_and_broadcasts(tmp_path):
    service, _logger, _config_file = _service(tmp_path)
    broadcasts = []
    service.connect_agent("agent-1", lambda agent_id, config: broadcasts.append((agent_id, config)))
    client = service.app.test_client()

    response = client.post(
        "/config/agent-1",
        json={
            "telemetry_batch_size": 25,
            "features_enabled": {"detailed_logging": True},
        },
    )

    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["agent_id"] == "agent-1"
    assert payload["config"]["telemetry_batch_size"] == 25
    assert payload["config"]["features_enabled"] == {
        "auto_remediation": True,
        "detailed_logging": True,
    }
    assert payload["broadcasted_to"] == 1
    assert broadcasts[0][0] == "agent-1"
    assert isinstance(broadcasts[0][1], AgentConfig)


def test_post_config_rejects_invalid_config(tmp_path):
    service, logger, _config_file = _service(tmp_path)
    client = service.app.test_client()

    response = client.post("/config/agent-1", json={"retry_max_attempts": 0})

    assert response.status_code == 400
    assert "retry_max_attempts" in response.get_json()["error"]
    assert logger.errors


def test_validate_endpoint_returns_valid_config(tmp_path):
    service, _logger, _config_file = _service(tmp_path)
    client = service.app.test_client()

    response = client.get("/config/agent-1/validate")

    assert response.status_code == 200
    assert response.get_json()["valid"] is True
    assert response.get_json()["errors"] == []


def test_validate_endpoint_returns_errors_for_invalid_current_config(tmp_path):
    service, _logger, _config_file = _service(
        tmp_path,
        config_data={"telemetry_batch_size": 0},
    )
    client = service.app.test_client()

    response = client.get("/config/agent-1/validate")

    assert response.status_code == 200
    assert response.get_json()["valid"] is False
    assert "telemetry_batch_size" in response.get_json()["errors"][0]


def test_health_metrics_and_cors_headers(tmp_path):
    service, _logger, _config_file = _service(tmp_path)
    client = service.app.test_client()

    health = client.get("/health")
    options = client.options("/config/agent-1")
    client.get("/config/agent-1")
    metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.get_json() == {"status": "healthy"}
    assert options.status_code == 204
    assert options.headers["Access-Control-Allow-Origin"] == "*"
    assert metrics.status_code == 200
    assert metrics.get_json()["config_requests"] == 1
    assert metrics.get_json()["cache_entries"] == 1
    assert metrics.get_json()["registered_agents"] == 1


def test_config_cache_hits_and_misses_are_tracked(tmp_path):
    service, _logger, _config_file = _service(tmp_path)
    client = service.app.test_client()

    client.get("/config/agent-1")
    client.get("/config/agent-1")
    metrics = client.get("/metrics").get_json()

    assert metrics["cache_misses"] == 1
    assert metrics["cache_hits"] == 1


def test_auto_reload_refreshes_after_five_minutes(tmp_path):
    clock = FakeClock()
    service, _logger, config_file = _service(
        tmp_path,
        config_data={"telemetry_batch_size": 10},
        clock=clock,
    )
    client = service.app.test_client()

    assert client.get("/config/agent-1").get_json()["telemetry_batch_size"] == 10
    config_file.write_text(json.dumps({"telemetry_batch_size": 20}), encoding="utf-8")
    assert client.get("/config/agent-1").get_json()["telemetry_batch_size"] == 10
    clock.advance(301)

    response = client.get("/config/agent-1")

    assert response.get_json()["telemetry_batch_size"] == 20
    assert client.get("/metrics").get_json()["reloads"] == 1


def test_missing_config_file_falls_back_to_defaults(tmp_path):
    service, _logger, config_file = _service(tmp_path)
    config_file.unlink(missing_ok=True)
    client = service.app.test_client()

    response = client.get("/config/agent-1")

    assert response.status_code == 200
    assert response.get_json() == AgentConfig().to_dict()


def test_create_config_service_app_returns_flask_app(tmp_path):
    app = create_config_service_app(
        config_file=str(tmp_path / "missing.json"),
        registered_agents={"agent-1"},
    )

    assert isinstance(app, Flask)


def test_default_structured_logger_is_supported(tmp_path):
    app = Flask(__name__)
    logger = StructuredLogger("config-service", logger=logging.getLogger("config-test"))
    service = ConfigServiceAPI(
        app=app,
        config_manager=ConfigManager(config_file=tmp_path / "missing.json"),
        registered_agents={"agent-1"},
        structured_logger=logger,
    )

    assert service.app.test_client().get("/health").status_code == 200
