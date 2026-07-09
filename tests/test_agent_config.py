import json
import os
import sys
from dataclasses import is_dataclass

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_config import AgentConfig, ConfigManager


class FakeClock:
    def __init__(self, current=1000.0):
        self.current = current

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += seconds


def test_agent_config_defaults_and_serialization():
    config = AgentConfig()

    assert is_dataclass(config)
    assert config.telemetry_batch_size == 50
    assert config.telemetry_batch_timeout_seconds == 5
    assert config.retry_max_attempts == 5
    assert config.retry_initial_delay_seconds == 1
    assert config.retry_max_delay_seconds == 60
    assert config.health_check_interval_seconds == 30
    assert config.alert_error_threshold_percent == 20
    assert config.alert_timeout_seconds == 300
    assert config.log_level == "INFO"
    assert config.features_enabled == {
        "auto_remediation": True,
        "detailed_logging": False,
    }

    payload = json.loads(config.to_json())
    assert payload == config.to_dict()


def test_from_dict_merges_defaults_and_feature_flags():
    config = AgentConfig.from_dict(
        {
            "telemetry_batch_size": 10,
            "log_level": "debug",
            "features_enabled": {"detailed_logging": True},
            "unknown": "ignored",
        }
    )

    assert config.telemetry_batch_size == 10
    assert config.telemetry_batch_timeout_seconds == 5
    assert config.log_level == "DEBUG"
    assert config.features_enabled == {
        "auto_remediation": True,
        "detailed_logging": True,
    }


def test_validate_rejects_invalid_values():
    with pytest.raises(ValueError, match="telemetry_batch_size"):
        AgentConfig(telemetry_batch_size=0)

    with pytest.raises(ValueError, match="retry_max_delay_seconds"):
        AgentConfig(retry_initial_delay_seconds=10, retry_max_delay_seconds=5)

    with pytest.raises(ValueError, match="alert_error_threshold_percent"):
        AgentConfig(alert_error_threshold_percent=101)

    with pytest.raises(ValueError, match="log_level"):
        AgentConfig(log_level="LOUD")

    with pytest.raises(TypeError, match="features_enabled"):
        AgentConfig(features_enabled={"auto_remediation": "yes"})


def test_config_manager_loads_json_defaults_and_agent_overrides(tmp_path):
    config_file = tmp_path / "agent-config.json"
    config_file.write_text(
        json.dumps(
            {
                "default": {
                    "telemetry_batch_size": 100,
                    "features_enabled": {"detailed_logging": True},
                },
                "agents": {
                    "canvas-tutor-agent": {
                        "retry_max_attempts": 8,
                        "log_level": "DEBUG",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manager = ConfigManager(config_file=config_file)

    canvas_config = manager.get_config("canvas-tutor-agent")
    other_config = manager.get_config("other-agent")

    assert canvas_config.telemetry_batch_size == 100
    assert canvas_config.retry_max_attempts == 8
    assert canvas_config.log_level == "DEBUG"
    assert canvas_config.features_enabled["detailed_logging"] is True

    assert other_config.telemetry_batch_size == 100
    assert other_config.retry_max_attempts == 5
    assert other_config.log_level == "INFO"


def test_config_manager_environment_overrides_file(monkeypatch, tmp_path):
    config_file = tmp_path / "agent-config.json"
    config_file.write_text(
        json.dumps({"telemetry_batch_size": 10, "log_level": "INFO"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONFIG_TELEMETRY_BATCH_SIZE", "25")
    monkeypatch.setenv("AGENT_CONFIG_RETRY_INITIAL_DELAY_SECONDS", "2.5")
    monkeypatch.setenv(
        "AGENT_CONFIG_FEATURES_ENABLED",
        json.dumps({"auto_remediation": False, "detailed_logging": True}),
    )

    manager = ConfigManager(config_file=config_file)
    config = manager.get_config("agent-1")

    assert config.telemetry_batch_size == 25
    assert config.retry_initial_delay_seconds == 2.5
    assert config.features_enabled == {
        "auto_remediation": False,
        "detailed_logging": True,
    }


def test_config_manager_uses_cache_until_ttl_expires(tmp_path):
    clock = FakeClock()
    config_file = tmp_path / "agent-config.json"
    config_file.write_text(json.dumps({"telemetry_batch_size": 10}), encoding="utf-8")
    manager = ConfigManager(config_file=config_file, cache_ttl_seconds=300, clock=clock.now)

    first = manager.get_config("agent-1")
    config_file.write_text(json.dumps({"telemetry_batch_size": 20}), encoding="utf-8")
    cached = manager.get_config("agent-1")
    clock.advance(301)
    refreshed = manager.get_config("agent-1")

    assert first.telemetry_batch_size == 10
    assert cached.telemetry_batch_size == 10
    assert refreshed.telemetry_batch_size == 20


def test_reload_config_refreshes_cache_immediately(tmp_path):
    config_file = tmp_path / "agent-config.json"
    config_file.write_text(json.dumps({"telemetry_batch_size": 10}), encoding="utf-8")
    manager = ConfigManager(config_file=config_file)

    assert manager.get_config("agent-1").telemetry_batch_size == 10
    config_file.write_text(json.dumps({"telemetry_batch_size": 30}), encoding="utf-8")
    manager.reload_config()

    assert manager.get_config("agent-1").telemetry_batch_size == 30


def test_set_config_applies_in_memory_agent_override(tmp_path):
    config_file = tmp_path / "agent-config.json"
    config_file.write_text(json.dumps({"telemetry_batch_size": 10}), encoding="utf-8")
    manager = ConfigManager(config_file=config_file)

    manager.set_config(
        "agent-1",
        {"telemetry_batch_size": 99, "features_enabled": {"detailed_logging": True}},
    )
    config = manager.get_config("agent-1")

    assert config.telemetry_batch_size == 99
    assert config.features_enabled["detailed_logging"] is True


def test_config_manager_missing_json_file_returns_defaults(tmp_path):
    manager = ConfigManager(config_file=tmp_path / "missing.json")

    assert manager.get_config("agent-1").to_dict() == AgentConfig().to_dict()


def test_config_manager_rejects_invalid_json(tmp_path):
    config_file = tmp_path / "agent-config.json"
    config_file.write_text("{bad json", encoding="utf-8")
    manager = ConfigManager(config_file=config_file)

    with pytest.raises(ValueError, match="Invalid JSON config file"):
        manager.get_config("agent-1")
