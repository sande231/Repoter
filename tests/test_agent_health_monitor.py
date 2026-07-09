import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_health_monitor import AgentHealthMonitor
from health_check import HealthCheck, HealthStatus


class FakeClock:
    def __init__(self, current):
        self.current = current

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


def _monitor(clock, **kwargs):
    return AgentHealthMonitor(
        agent_id="agent-1",
        clock=clock.now,
        queue_depth_provider=lambda: 7,
        **kwargs,
    )


def test_get_health_check_returns_healthy_snapshot_with_metrics():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    clock.advance(10)
    monitor.record_heartbeat()
    monitor.record_task_completion()

    health = monitor.get_health_check()

    assert isinstance(health, HealthCheck)
    assert health.status is HealthStatus.HEALTHY
    assert health.is_healthy() is True
    assert health.uptime_seconds == 10
    assert health.error_rate_percent == 0
    assert health.metrics["queue_depth"] == 7
    assert health.metrics["memory_mb"] >= 0
    assert health.metrics["cpu_percent"] >= 0
    assert health.metrics["heartbeat_age_seconds"] == 0


def test_error_rate_over_five_minute_window_degrades_and_unhealthy():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    monitor.record_heartbeat()

    monitor.record_error()
    for _ in range(9):
        monitor.record_task_completion()

    degraded = monitor.get_health_check()
    assert degraded.status is HealthStatus.DEGRADED
    assert degraded.error_rate_percent == 10

    monitor.reset_metrics()
    monitor.record_heartbeat()
    for _ in range(3):
        monitor.record_error()
    for _ in range(7):
        monitor.record_task_completion()

    unhealthy = monitor.get_health_check()
    assert unhealthy.status is HealthStatus.UNHEALTHY
    assert unhealthy.error_rate_percent == 30


def test_sliding_window_prunes_old_task_errors():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock, window_seconds=300)
    monitor.record_heartbeat()
    monitor.record_error()

    clock.advance(301)
    monitor.record_heartbeat()
    health = monitor.get_health_check()

    assert health.status is HealthStatus.HEALTHY
    assert health.error_rate_percent == 0
    assert health.metrics["task_count_window"] == 0


def test_dependency_status_controls_overall_health():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    monitor.record_heartbeat()

    monitor.set_dependency_status("canvas", "degraded")
    degraded = monitor.get_health_check()
    assert degraded.status is HealthStatus.DEGRADED
    assert degraded.dependencies == {"canvas": HealthStatus.DEGRADED}

    monitor.set_dependency_status("canvas", HealthStatus.UNHEALTHY)
    unhealthy = monitor.get_health_check()
    assert unhealthy.status is HealthStatus.UNHEALTHY


def test_stale_heartbeat_degrades_then_unhealthy():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    monitor.record_heartbeat()

    clock.advance(121)
    degraded = monitor.get_health_check()
    assert degraded.status is HealthStatus.DEGRADED
    assert degraded.metrics["heartbeat_age_seconds"] == 121

    clock.advance(180)
    unhealthy = monitor.get_health_check()
    assert unhealthy.status is HealthStatus.UNHEALTHY
    assert unhealthy.metrics["heartbeat_age_seconds"] == 301


def test_record_task_completion_updates_last_task():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    clock.advance(30)

    monitor.record_task_completion()

    assert monitor.get_health_check().last_task == clock.current


def test_reset_metrics_clears_task_window_and_resets_timing():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)
    monitor.record_error()
    clock.advance(60)

    monitor.reset_metrics()
    health = monitor.get_health_check()

    assert health.status is HealthStatus.HEALTHY
    assert health.uptime_seconds == 0
    assert health.error_rate_percent == 0
    assert health.metrics["task_count_window"] == 0


def test_invalid_dependency_status_raises_value_error():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))
    monitor = _monitor(clock)

    with pytest.raises(ValueError, match="Invalid health status"):
        monitor.set_dependency_status("database", "offline")


def test_queue_depth_provider_failure_is_reported_as_negative_one():
    clock = FakeClock(datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc))

    def bad_queue_depth():
        raise RuntimeError("queue unavailable")

    monitor = AgentHealthMonitor(
        agent_id="agent-1",
        clock=clock.now,
        queue_depth_provider=bad_queue_depth,
    )

    health = monitor.get_health_check()

    assert health.metrics["queue_depth"] == -1
