"""Async lifecycle primitives for Synapse agents.

Example:

    import asyncio

    from agent_lifecycle import Agent, LifecycleManager

    class CanvasTutorAgent(Agent):
        async def on_startup(self):
            self.config.validate()
            # Open Canvas/API clients here.

        async def on_shutdown(self):
            # Flush queues and close clients here.
            pass

    async def main():
        manager = LifecycleManager()
        manager.register_agent(CanvasTutorAgent(agent_id="canvas-tutor-agent"))
        await manager.start_all_agents()
        await manager.stop_all_agents()

    asyncio.run(main())
"""

from __future__ import annotations

from abc import ABC
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import inspect
import logging
import time
from typing import Any

from agent_config import AgentConfig
from structured_logger import StructuredLogger


LifecycleCallback = Callable[[], Any | Awaitable[Any]]


class AgentState:
    """Runtime states used by Agent and LifecycleManager."""

    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass
class LifecycleResult:
    """Outcome of one lifecycle operation."""

    agent_id: str
    event_name: str
    success: bool
    duration_ms: float
    state: str
    error_message: str = ""
    recovery_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


class Agent(ABC):
    """Abstract async base class for agents with safe lifecycle wrappers.

    Override the `on_*` hooks in subclasses. Production callers should invoke
    `start()`, `shutdown()`, `health_check_failed()`, `apply_config_update()`,
    and `handle_error()` so every hook gets structured logging, timeout
    handling, error capture, and recovery suggestions.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        config: AgentConfig | None = None,
        logger: StructuredLogger | None = None,
        lifecycle_timeout_seconds: float = 30,
    ) -> None:
        if not agent_id:
            raise ValueError("agent_id is required")
        if lifecycle_timeout_seconds <= 0:
            raise ValueError("lifecycle_timeout_seconds must be greater than zero")

        self.agent_id = agent_id
        self.config = config or AgentConfig()
        self.logger = logger or StructuredLogger(
            agent_id,
            logger=logging.getLogger(f"agent.{agent_id}"),
        )
        self.lifecycle_timeout_seconds = float(lifecycle_timeout_seconds)
        self.state = AgentState.INITIALIZED
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.last_error: str = ""
        self.last_lifecycle_result: LifecycleResult | None = None

    async def on_startup(self) -> None:
        """Initialize connections, load config, and validate dependencies."""

    async def on_shutdown(self) -> None:
        """Flush queues, close connections, and clean up resources."""

    async def on_health_check_fail(self) -> None:
        """Attempt self-healing when the agent health check fails."""

    async def on_config_update(self, new_config: AgentConfig) -> None:
        """Apply a new config without restart when possible."""
        self.config = new_config

    async def on_error(self, exception: Exception, context: dict[str, Any]) -> None:
        """Log error details and update health or runtime status."""

    async def start(self) -> LifecycleResult:
        """Run `on_startup()` with logging, timeout, and error handling."""
        self.state = AgentState.STARTING
        result = await self._run_lifecycle_hook(
            "on_startup",
            self.on_startup,
            success_state=AgentState.RUNNING,
            failure_state=AgentState.ERROR,
            timeout_seconds=self.lifecycle_timeout_seconds,
        )
        if result.success:
            self.started_at = self._utc_now()
        return result

    async def shutdown(self) -> LifecycleResult:
        """Run `on_shutdown()` with a maximum timeout of 30 seconds."""
        self.state = AgentState.STOPPING
        timeout_seconds = min(30.0, self.lifecycle_timeout_seconds)
        result = await self._run_lifecycle_hook(
            "on_shutdown",
            self.on_shutdown,
            success_state=AgentState.STOPPED,
            failure_state=AgentState.ERROR,
            timeout_seconds=timeout_seconds,
        )
        if result.success:
            self.stopped_at = self._utc_now()
        return result

    async def health_check_failed(self) -> LifecycleResult:
        """Run `on_health_check_fail()` and mark the agent degraded on failure."""
        return await self._run_lifecycle_hook(
            "on_health_check_fail",
            self.on_health_check_fail,
            success_state=AgentState.DEGRADED,
            failure_state=AgentState.ERROR,
            timeout_seconds=self.lifecycle_timeout_seconds,
        )

    async def apply_config_update(self, new_config: AgentConfig | dict[str, Any]) -> LifecycleResult:
        """Validate and apply config via `on_config_update()`."""
        resolved_config = (
            new_config
            if isinstance(new_config, AgentConfig)
            else AgentConfig.from_dict(new_config)
        )
        return await self._run_lifecycle_hook(
            "on_config_update",
            lambda: self.on_config_update(resolved_config),
            success_state=self.state,
            failure_state=AgentState.ERROR,
            timeout_seconds=self.lifecycle_timeout_seconds,
            metadata={"new_config": resolved_config.to_dict()},
        )

    async def handle_error(
        self,
        exception: Exception,
        context: dict[str, Any] | None = None,
    ) -> LifecycleResult:
        """Run `on_error()` after an exception and keep structured context."""
        context = dict(context or {})
        self.last_error = str(exception)
        return await self._run_lifecycle_hook(
            "on_error",
            lambda: self.on_error(exception, context),
            success_state=AgentState.DEGRADED,
            failure_state=AgentState.ERROR,
            timeout_seconds=self.lifecycle_timeout_seconds,
            metadata={"context": context, "exception": repr(exception)},
        )

    async def _run_lifecycle_hook(
        self,
        event_name: str,
        hook: Callable[[], Any | Awaitable[Any]],
        *,
        success_state: str,
        failure_state: str,
        timeout_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> LifecycleResult:
        """Execute one lifecycle hook with timeout and structured logging."""
        metadata = dict(metadata or {})
        self.logger.log_event(
            "lifecycle_start",
            lifecycle_event=event_name,
            state=self.state,
            timeout_seconds=timeout_seconds,
            **metadata,
        )
        started = time.perf_counter()

        try:
            await asyncio.wait_for(
                self._maybe_await(hook()),
                timeout=timeout_seconds,
            )
            duration_ms = self._duration_ms(started)
            self.state = success_state
            if event_name != "on_error":
                self.last_error = ""
            result = LifecycleResult(
                agent_id=self.agent_id,
                event_name=event_name,
                success=True,
                duration_ms=duration_ms,
                state=self.state,
            )
            self.logger.log_event(
                "lifecycle_success",
                lifecycle_event=event_name,
                state=self.state,
                duration_ms=duration_ms,
            )
            self.last_lifecycle_result = result
            return result
        except asyncio.TimeoutError as exc:
            duration_ms = self._duration_ms(started)
            self.state = failure_state
            self.last_error = f"{event_name} timed out after {timeout_seconds:g}s"
            suggestions = self.recovery_suggestions(event_name, exc)
            result = LifecycleResult(
                agent_id=self.agent_id,
                event_name=event_name,
                success=False,
                duration_ms=duration_ms,
                state=self.state,
                error_message=self.last_error,
                recovery_suggestions=suggestions,
            )
            self.logger.log_error(
                exc,
                {
                    "event_name": "lifecycle_timeout",
                    "lifecycle_event": event_name,
                    "timeout_seconds": timeout_seconds,
                    "duration_ms": duration_ms,
                    "recovery_suggestions": suggestions,
                },
            )
            self.last_lifecycle_result = result
            return result
        except Exception as exc:
            duration_ms = self._duration_ms(started)
            self.state = failure_state
            self.last_error = str(exc)
            suggestions = self.recovery_suggestions(event_name, exc)
            result = LifecycleResult(
                agent_id=self.agent_id,
                event_name=event_name,
                success=False,
                duration_ms=duration_ms,
                state=self.state,
                error_message=str(exc),
                recovery_suggestions=suggestions,
            )
            self.logger.log_error(
                exc,
                {
                    "event_name": "lifecycle_error",
                    "lifecycle_event": event_name,
                    "duration_ms": duration_ms,
                    "recovery_suggestions": suggestions,
                    **metadata,
                },
            )
            self.last_lifecycle_result = result
            return result

    def recovery_suggestions(self, event_name: str, exception: Exception) -> list[str]:
        """Return practical recovery suggestions for a failed lifecycle event."""
        suggestions = {
            "on_startup": [
                "Validate configuration and required environment variables.",
                "Check network access and dependency credentials.",
                "Retry startup after dependencies become healthy.",
            ],
            "on_shutdown": [
                "Force-close blocked connections after the shutdown timeout.",
                "Inspect queue flushes and resource cleanup tasks for deadlocks.",
            ],
            "on_health_check_fail": [
                "Refresh dependency status and restart degraded connections.",
                "Drain local queues and verify heartbeat publishing.",
            ],
            "on_config_update": [
                "Validate the new configuration payload.",
                "Roll back to the last known good configuration if hot reload fails.",
            ],
            "on_error": [
                "Capture the failing task context and quarantine unsafe work.",
                "Retry with exponential backoff only when the operation is idempotent.",
            ],
        }
        return suggestions.get(
            event_name,
            ["Inspect logs, verify dependencies, and retry the lifecycle operation."],
        )

    @staticmethod
    async def _maybe_await(value: Any | Awaitable[Any]) -> Any:
        """Await a value only when it is awaitable."""
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _utc_now() -> datetime:
        """Return the current UTC time."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _duration_ms(started: float) -> float:
        """Return elapsed milliseconds from a `time.perf_counter()` start."""
        return round((time.perf_counter() - started) * 1000, 3)

    def status_snapshot(self) -> dict[str, Any]:
        """Return current agent lifecycle status."""
        return {
            "agent_id": self.agent_id,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "last_error": self.last_error,
            "last_lifecycle_result": (
                self.last_lifecycle_result.to_dict()
                if self.last_lifecycle_result
                else None
            ),
        }


class LifecycleManager:
    """Coordinate async lifecycle events across multiple agents.

    Example:

        manager = LifecycleManager()
        manager.register_agent(my_agent)
        await manager.start_all_agents()
        status = manager.get_agent_status(my_agent.agent_id)
        await manager.stop_all_agents()
    """

    def __init__(
        self,
        *,
        logger: StructuredLogger | None = None,
        shutdown_timeout_seconds: float = 30,
    ) -> None:
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be greater than zero")
        self.agents: dict[str, Agent] = {}
        self.shutdown_hooks: list[LifecycleCallback] = []
        self.logger = logger or StructuredLogger(
            "lifecycle-manager",
            logger=logging.getLogger("agent_lifecycle.manager"),
        )
        self.shutdown_timeout_seconds = min(30.0, float(shutdown_timeout_seconds))
        self._lock = asyncio.Lock()

    def register_agent(self, agent: Agent) -> None:
        """Register one agent for managed lifecycle operations."""
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent instance")
        if agent.agent_id in self.agents:
            raise ValueError(f"agent {agent.agent_id!r} is already registered")
        self.agents[agent.agent_id] = agent
        self.logger.log_event("agent_registered", target_agent_id=agent.agent_id)

    def add_shutdown_hook(self, callback: LifecycleCallback) -> None:
        """Add a sync or async callback run after agents stop."""
        self.shutdown_hooks.append(callback)

    async def start_all_agents(self) -> dict[str, LifecycleResult]:
        """Start all registered agents concurrently."""
        async with self._lock:
            self.logger.log_event("lifecycle_manager_start_all", agent_count=len(self.agents))
            results = await asyncio.gather(
                *(agent.start() for agent in self.agents.values())
            )
            return {result.agent_id: result for result in results}

    async def stop_all_agents(self) -> dict[str, LifecycleResult]:
        """Stop all registered agents concurrently, then run shutdown hooks."""
        async with self._lock:
            self.logger.log_event("lifecycle_manager_stop_all", agent_count=len(self.agents))
            results = await asyncio.gather(
                *(agent.shutdown() for agent in self.agents.values())
            )
            await self._run_shutdown_hooks()
            return {result.agent_id: result for result in results}

    def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        """Return current status for one registered agent."""
        agent = self.agents.get(agent_id)
        if not agent:
            return None
        return agent.status_snapshot()

    async def _run_shutdown_hooks(self) -> None:
        """Run all registered shutdown hooks with timeout/error handling."""
        for callback in self.shutdown_hooks:
            started = time.perf_counter()
            try:
                await asyncio.wait_for(
                    Agent._maybe_await(callback()),
                    timeout=self.shutdown_timeout_seconds,
                )
                self.logger.log_event(
                    "shutdown_hook_success",
                    duration_ms=Agent._duration_ms(started),
                )
            except asyncio.TimeoutError as exc:
                self.logger.log_error(
                    exc,
                    {
                        "event_name": "shutdown_hook_timeout",
                        "duration_ms": Agent._duration_ms(started),
                        "recovery_suggestions": [
                            "Inspect the shutdown hook for blocked IO or unbounded waits.",
                            "Move non-critical cleanup work to a background recovery task.",
                        ],
                    },
                )
            except Exception as exc:
                self.logger.log_error(
                    exc,
                    {
                        "event_name": "shutdown_hook_error",
                        "duration_ms": Agent._duration_ms(started),
                        "recovery_suggestions": [
                            "Review shutdown hook logs and make cleanup idempotent.",
                        ],
                    },
                )
