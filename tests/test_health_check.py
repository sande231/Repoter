import json
import os
import sys
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from health_check import HealthCheck, HealthStatus


def test_health_check_is_dataclass_with_expected_fields():
    assert is_dataclass(HealthCheck)
    assert [field.name for field in fields(HealthCheck)] == [
        "status",
        "uptime_seconds",
        "last_task",
        "error_rate_percent",
        "dependencies",
        "metrics",
    ]


def test_to_dict_returns_json_serializable_values():
    last_task = datetime(2026, 6, 28, 12, 30, tzinfo=timezone.utc)
    health = HealthCheck(
        status=HealthStatus.HEALTHY,
        uptime_seconds=120,
        last_task=last_task,
        error_rate_percent=2.5,
        dependencies={
            "canvas": HealthStatus.DEGRADED,
            "ingestion": HealthStatus.HEALTHY,
        },
        metrics={"memory_mb": 128, "cpu_percent": 12.4, "queue_depth": 3},
    )

    assert health.to_dict() == {
        "status": "healthy",
        "uptime_seconds": 120,
        "last_task": "2026-06-28T12:30:00Z",
        "error_rate_percent": 2.5,
        "dependencies": {
            "canvas": "degraded",
            "ingestion": "healthy",
        },
        "metrics": {
            "memory_mb": 128,
            "cpu_percent": 12.4,
            "queue_depth": 3,
        },
    }


def test_to_json_returns_json_string():
    health = HealthCheck(
        status="degraded",
        uptime_seconds=10,
        last_task=datetime(2026, 6, 28, 1, 2, 3),
        error_rate_percent=5,
        dependencies={"database": "healthy"},
        metrics={},
    )

    payload = json.loads(health.to_json())

    assert payload["status"] == "degraded"
    assert payload["last_task"] == "2026-06-28T01:02:03Z"
    assert payload["dependencies"] == {"database": "healthy"}


def test_status_helper_methods():
    healthy = HealthCheck(
        status=HealthStatus.HEALTHY,
        uptime_seconds=1,
        last_task=datetime.now(timezone.utc),
        error_rate_percent=0,
    )
    degraded = HealthCheck(
        status=HealthStatus.DEGRADED,
        uptime_seconds=1,
        last_task=datetime.now(timezone.utc),
        error_rate_percent=20,
    )

    assert healthy.is_healthy() is True
    assert healthy.is_degraded() is False
    assert degraded.is_healthy() is False
    assert degraded.is_degraded() is True


def test_invalid_status_raises_value_error():
    with pytest.raises(ValueError, match="Invalid health status"):
        HealthCheck(
            status="offline",
            uptime_seconds=1,
            last_task=datetime.now(timezone.utc),
            error_rate_percent=0,
        )


def test_invalid_numeric_fields_raise_value_error():
    with pytest.raises(ValueError, match="uptime_seconds"):
        HealthCheck(
            status=HealthStatus.HEALTHY,
            uptime_seconds=-1,
            last_task=datetime.now(timezone.utc),
            error_rate_percent=0,
        )

    with pytest.raises(ValueError, match="error_rate_percent"):
        HealthCheck(
            status=HealthStatus.HEALTHY,
            uptime_seconds=1,
            last_task=datetime.now(timezone.utc),
            error_rate_percent=101,
        )
