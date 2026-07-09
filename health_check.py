"""Health check data model for Synapse agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any


class HealthStatus(str, Enum):
    """Allowed health states for agents and dependencies."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheck:
    """Structured health snapshot for an agent.

    Attributes:
        status: Overall agent health status.
        uptime_seconds: Number of seconds the agent has been running.
        last_task: Datetime when the last task completed.
        error_rate_percent: Percentage of failed tasks.
        dependencies: Dependency name to dependency health status.
        metrics: Runtime metrics such as memory_mb, cpu_percent, and queue_depth.
    """

    status: HealthStatus
    uptime_seconds: int
    last_task: datetime
    error_rate_percent: float
    dependencies: dict[str, HealthStatus] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize enum-like input and validate numeric fields."""
        self.status = self._coerce_status(self.status)
        self.dependencies = {
            name: self._coerce_status(status)
            for name, status in self.dependencies.items()
        }

        if not isinstance(self.last_task, datetime):
            raise TypeError("last_task must be a datetime instance")
        if self.uptime_seconds < 0:
            raise ValueError("uptime_seconds must be non-negative")
        if not 0 <= self.error_rate_percent <= 100:
            raise ValueError("error_rate_percent must be between 0 and 100")

    @staticmethod
    def _coerce_status(status: HealthStatus | str) -> HealthStatus:
        """Convert a string or HealthStatus into a HealthStatus value."""
        if isinstance(status, HealthStatus):
            return status
        try:
            return HealthStatus(str(status).lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in HealthStatus)
            raise ValueError(f"Invalid health status {status!r}; expected one of: {allowed}") from exc

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        """Return an ISO 8601 timestamp string."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        data = asdict(self)
        data["status"] = self.status.value
        data["last_task"] = self._format_datetime(self.last_task)
        data["dependencies"] = {
            name: status.value
            for name, status in self.dependencies.items()
        }
        return data

    def to_json(self) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict(), sort_keys=True)

    def is_healthy(self) -> bool:
        """Return True when the overall status is healthy."""
        return self.status is HealthStatus.HEALTHY

    def is_degraded(self) -> bool:
        """Return True when the overall status is degraded."""
        return self.status is HealthStatus.DEGRADED
