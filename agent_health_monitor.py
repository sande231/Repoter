"""Runtime health monitor for Synapse agents."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any

import psutil

from health_check import HealthCheck, HealthStatus


Clock = Callable[[], datetime]
QueueDepthProvider = Callable[[], int]


@dataclass(frozen=True)
class TaskEvent:
    """Task result tracked inside the error-rate sliding window."""

    timestamp: datetime
    failed: bool


class AgentHealthMonitor:
    """Track heartbeat, dependency, task, and system health for one agent."""

    def __init__(
        self,
        *,
        agent_id: str,
        clock: Clock | None = None,
        queue_depth_provider: QueueDepthProvider | None = None,
        window_seconds: int = 300,
        degraded_heartbeat_seconds: int = 120,
        unhealthy_heartbeat_seconds: int = 300,
    ) -> None:
        self.agent_id = agent_id
        self._clock = clock or self._utc_now
        self.queue_depth_provider = queue_depth_provider
        self.window_seconds = int(window_seconds)
        self.degraded_heartbeat_seconds = int(degraded_heartbeat_seconds)
        self.unhealthy_heartbeat_seconds = int(unhealthy_heartbeat_seconds)
        self.start_time = self._now()
        self.last_heartbeat = self.start_time
        self.last_task = self.start_time
        self.dependencies: dict[str, HealthStatus] = {}
        self._task_events: deque[TaskEvent] = deque()
        self._process = psutil.Process(os.getpid())

        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.degraded_heartbeat_seconds < 0:
            raise ValueError("degraded_heartbeat_seconds must be non-negative")
        if self.unhealthy_heartbeat_seconds < self.degraded_heartbeat_seconds:
            raise ValueError(
                "unhealthy_heartbeat_seconds must be greater than or equal to "
                "degraded_heartbeat_seconds"
            )

    @staticmethod
    def _utc_now() -> datetime:
        """Return the current UTC time."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        """Return a timezone-aware UTC datetime."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _now(self) -> datetime:
        """Return the monitor clock time normalized to UTC."""
        return self._normalize_datetime(self._clock())

    def record_heartbeat(self) -> None:
        """Record that the agent is alive now."""
        self.last_heartbeat = self._now()

    def record_error(self) -> None:
        """Record a failed task in the sliding error-rate window."""
        now = self._now()
        self.last_task = now
        self._task_events.append(TaskEvent(timestamp=now, failed=True))
        self._prune_task_events(now)

    def record_task_completion(self) -> None:
        """Record a successful task completion in the sliding window."""
        now = self._now()
        self.last_task = now
        self._task_events.append(TaskEvent(timestamp=now, failed=False))
        self._prune_task_events(now)

    def set_dependency_status(self, name: str, status: HealthStatus | str) -> None:
        """Set a dependency health status by dependency name."""
        if not name:
            raise ValueError("dependency name is required")
        self.dependencies[name] = HealthCheck._coerce_status(status)

    def reset_metrics(self) -> None:
        """Clear task metrics and reset timing baselines."""
        now = self._now()
        self.start_time = now
        self.last_heartbeat = now
        self.last_task = now
        self._task_events.clear()

    def _prune_task_events(self, now: datetime | None = None) -> None:
        """Drop task results older than the configured sliding window."""
        current_time = now or self._now()
        cutoff = current_time - timedelta(seconds=self.window_seconds)
        while self._task_events and self._task_events[0].timestamp < cutoff:
            self._task_events.popleft()

    def _error_rate_percent(self, now: datetime | None = None) -> float:
        """Return failed task percentage over the sliding window."""
        self._prune_task_events(now)
        total = len(self._task_events)
        if total == 0:
            return 0.0
        failures = sum(1 for event in self._task_events if event.failed)
        return round((failures / total) * 100, 2)

    def _heartbeat_age_seconds(self, now: datetime) -> int:
        """Return seconds since the latest heartbeat."""
        return max(0, int((now - self.last_heartbeat).total_seconds()))

    def _status_from_rules(self, now: datetime, error_rate_percent: float) -> HealthStatus:
        """Apply unhealthy and degraded rules to produce an overall status."""
        heartbeat_age = self._heartbeat_age_seconds(now)
        dependency_values = set(self.dependencies.values())

        if (
            error_rate_percent > 20
            or HealthStatus.UNHEALTHY in dependency_values
            or heartbeat_age > self.unhealthy_heartbeat_seconds
        ):
            return HealthStatus.UNHEALTHY

        if (
            error_rate_percent > 5
            or HealthStatus.DEGRADED in dependency_values
            or heartbeat_age > self.degraded_heartbeat_seconds
        ):
            return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY

    def _queue_depth(self) -> int:
        """Return queue depth from the configured provider, or zero."""
        if not self.queue_depth_provider:
            return 0
        try:
            return int(self.queue_depth_provider())
        except Exception:
            return -1

    def _system_metrics(self, now: datetime, error_rate_percent: float) -> dict[str, Any]:
        """Collect process and monitor metrics for health reporting."""
        memory_mb = self._process.memory_info().rss / (1024 * 1024)
        cpu_percent = self._process.cpu_percent(interval=None)
        return {
            "memory_mb": round(memory_mb, 2),
            "cpu_percent": round(float(cpu_percent), 2),
            "queue_depth": self._queue_depth(),
            "heartbeat_age_seconds": self._heartbeat_age_seconds(now),
            "task_window_seconds": self.window_seconds,
            "task_count_window": len(self._task_events),
            "error_rate_percent": error_rate_percent,
        }

    def get_health_check(self) -> HealthCheck:
        """Return a HealthCheck snapshot for the current monitor state."""
        now = self._now()
        error_rate_percent = self._error_rate_percent(now)
        status = self._status_from_rules(now, error_rate_percent)
        uptime_seconds = max(0, int((now - self.start_time).total_seconds()))

        return HealthCheck(
            status=status,
            uptime_seconds=uptime_seconds,
            last_task=self.last_task,
            error_rate_percent=error_rate_percent,
            dependencies=dict(self.dependencies),
            metrics=self._system_metrics(now, error_rate_percent),
        )
