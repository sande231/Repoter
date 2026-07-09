"""Graceful shutdown primitives for Synapse agents.

Example:

    import time

    from agent_sdk import AgentSDK
    from graceful_shutdown import GracefulShutdownHandler
    from local_queue import LocalQueue

    sdk = AgentSDK(agent_id="reporter", ingestion_url="http://localhost:5000")
    queue = LocalQueue("telemetry_queue.db")

    shutdown = GracefulShutdownHandler(timeout_seconds=30)
    shutdown.register_shutdown_callback(sdk.flush_local_queue)
    shutdown.register_queue_flusher(queue.clear_sent)
    shutdown.register_connection_closer(queue.close)
    shutdown.start()

    while not shutdown.is_shutting_down():
        with shutdown.track_task("collect-repo-status"):
            # Do one unit of work. New tasks should check can_accept_tasks().
            time.sleep(1)

    shutdown.wait_for_shutdown()
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import logging
import signal
import sys
import threading
import time
from types import FrameType
from typing import Any

from structured_logger import StructuredLogger


ShutdownCallback = Callable[[], Any | Awaitable[Any]]


class ShutdownTimeoutError(TimeoutError):
    """Raised internally when graceful shutdown exceeds its timeout."""


@dataclass
class ShutdownResult:
    """Summary of a completed graceful shutdown sequence."""

    exit_status: int
    timed_out: bool
    duration_ms: float
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable shutdown summary."""
        return {
            "exit_status": self.exit_status,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "errors": list(self.errors),
        }


class ShutdownManager:
    """Coordinate clean process shutdown for one or more agents.

    The manager handles SIGTERM/SIGINT, stops new work immediately, waits for
    tracked in-flight tasks, flushes queues, closes connections, and records an
    exit status. It supports sync and async callbacks, regular and async context
    managers, and repeated signals without running cleanup twice.
    """

    def __init__(
        self,
        timeout_seconds: float = 30,
        logger: StructuredLogger | logging.Logger | None = None,
        *,
        exit_on_shutdown: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self.timeout_seconds = float(timeout_seconds)
        self.logger = logger or StructuredLogger(
            "shutdown-manager",
            logger=logging.getLogger("graceful_shutdown"),
        )
        self.exit_on_shutdown = exit_on_shutdown

        self._shutdown_callbacks: list[ShutdownCallback] = []
        self._queue_flushers: list[ShutdownCallback] = []
        self._connection_closers: list[ShutdownCallback] = []
        self._previous_signal_handlers: dict[int, Any] = {}

        self._lock = threading.RLock()
        self._complete_event = threading.Event()
        self._inflight_complete = threading.Event()
        self._inflight_complete.set()

        self._started = False
        self._shutting_down = False
        self._shutdown_started = False
        self._accepting_tasks = True
        self._inflight_count = 0
        self._exit_status: int | None = None
        self._result: ShutdownResult | None = None
        self._errors: list[str] = []
        self._shutdown_thread: threading.Thread | None = None

    def register_shutdown_callback(self, callback: ShutdownCallback) -> None:
        """Register a sync or async cleanup function called during shutdown."""
        self._validate_callback(callback)
        with self._lock:
            self._shutdown_callbacks.append(callback)

    def register_queue_flusher(self, callback: ShutdownCallback) -> None:
        """Register a sync or async callback that flushes queued telemetry."""
        self._validate_callback(callback)
        with self._lock:
            self._queue_flushers.append(callback)

    def register_connection_closer(self, callback: ShutdownCallback) -> None:
        """Register a sync or async callback that closes connections/resources."""
        self._validate_callback(callback)
        with self._lock:
            self._connection_closers.append(callback)

    def start(self) -> "ShutdownManager":
        """Start listening for SIGTERM and SIGINT signals."""
        with self._lock:
            if self._started:
                self._log_event(
                    "shutdown_signal_handlers_already_started",
                    message="Shutdown signal handlers already started",
                )
                return self

            for sig in (signal.SIGTERM, signal.SIGINT):
                self._previous_signal_handlers[int(sig)] = signal.getsignal(sig)
                signal.signal(sig, self._handle_signal)
            self._started = True

        self._log_event(
            "shutdown_signal_handlers_started",
            message="Shutdown signal handlers started",
            signals=["SIGTERM", "SIGINT"],
        )
        return self

    def shutdown_gracefully(self) -> int:
        """Initiate a graceful shutdown from synchronous code.

        Returns process-style exit status `0` when shutdown completed before the
        timeout and `1` when the timeout was exceeded. If `exit_on_shutdown` is
        true, this method exits the process with that status after cleanup.
        """
        status = self._run_coroutine_sync(self.shutdown_gracefully_async())
        if self.exit_on_shutdown:
            sys.exit(status)
        return status

    async def shutdown_gracefully_async(self) -> int:
        """Initiate a graceful shutdown from asyncio code."""
        already_started = False
        with self._lock:
            if self._shutdown_started:
                self._log_event(
                    "shutdown_already_in_progress",
                    message="Shutdown already in progress",
                )
                already_started = True

        if already_started:
            await self._wait_for_shutdown_async()
        else:
            await self._run_shutdown_once()
        status = self._exit_status if self._exit_status is not None else 1
        if self.exit_on_shutdown:
            sys.exit(status)
        return status

    def is_shutting_down(self) -> bool:
        """Return True after a shutdown signal or manual shutdown request."""
        with self._lock:
            return self._shutting_down

    def wait_for_shutdown(self) -> int:
        """Block until graceful shutdown has completed and return exit status."""
        self._complete_event.wait()
        return self._exit_status if self._exit_status is not None else 1

    def can_accept_tasks(self) -> bool:
        """Return True when callers may start new work."""
        with self._lock:
            return self._accepting_tasks

    def task_started(self, task_name: str | None = None) -> bool:
        """Record a new in-flight task unless shutdown has started.

        Returns False when the manager is already shutting down and new work
        should be rejected.
        """
        with self._lock:
            if not self._accepting_tasks:
                self._log_event(
                    "shutdown_task_rejected",
                    message="Task rejected because shutdown is in progress",
                    task_name=task_name,
                )
                return False
            self._inflight_count += 1
            self._inflight_complete.clear()
            self._log_event(
                "shutdown_task_started",
                message="In-flight task started",
                task_name=task_name,
                inflight_count=self._inflight_count,
            )
            return True

    def task_finished(self, task_name: str | None = None) -> None:
        """Mark one tracked task as complete."""
        with self._lock:
            if self._inflight_count > 0:
                self._inflight_count -= 1
            if self._inflight_count == 0:
                self._inflight_complete.set()
            self._log_event(
                "shutdown_task_finished",
                message="In-flight task finished",
                task_name=task_name,
                inflight_count=self._inflight_count,
            )

    @contextmanager
    def track_task(self, task_name: str | None = None) -> Iterator[bool]:
        """Context manager that tracks one synchronous in-flight task."""
        started = self.task_started(task_name)
        try:
            yield started
        finally:
            if started:
                self.task_finished(task_name)

    @asynccontextmanager
    async def track_async_task(self, task_name: str | None = None):
        """Async context manager that tracks one in-flight task."""
        started = self.task_started(task_name)
        try:
            yield started
        finally:
            if started:
                self.task_finished(task_name)

    @property
    def result(self) -> ShutdownResult | None:
        """Return the shutdown result after completion."""
        return self._result

    @property
    def inflight_count(self) -> int:
        """Return the number of tasks currently being drained."""
        with self._lock:
            return self._inflight_count

    def __enter__(self) -> "ShutdownManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown_gracefully()

    async def __aenter__(self) -> "ShutdownManager":
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.shutdown_gracefully_async()

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        signal_name = self._signal_name(signum)
        self._log_event(
            "shutdown_signal_received",
            message="Shutdown signal received",
            signal=signal_name,
        )
        with self._lock:
            if self._shutdown_started or (
                self._shutdown_thread is not None
                and self._shutdown_thread.is_alive()
            ):
                self._log_event(
                    "shutdown_signal_ignored",
                    message="Shutdown already in progress",
                    signal=signal_name,
                )
                return
            self._shutting_down = True
            self._accepting_tasks = False
            self._shutdown_thread = threading.Thread(
                target=self.shutdown_gracefully,
                name="synapse-graceful-shutdown",
                daemon=True,
            )

        self._shutdown_thread.start()

    async def _run_shutdown_once(self) -> None:
        with self._lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
            self._shutting_down = True
            self._accepting_tasks = False
            self._errors = []

        started = time.perf_counter()
        self._complete_event.clear()
        self._log_event(
            "shutdown_signal_received",
            message="Shutdown signal received",
            timeout_seconds=self.timeout_seconds,
        )

        timed_out = False
        try:
            await asyncio.wait_for(
                self._run_shutdown_steps(started),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            timed_out = True
            self._record_error("Graceful shutdown timed out")
            self._log_error(
                exc,
                {
                    "event_name": "shutdown_timeout",
                    "timeout_seconds": self.timeout_seconds,
                    "duration_ms": self._duration_ms(started),
                    "recovery_suggestions": [
                        "Inspect callbacks and in-flight tasks for blocking IO.",
                        "Make cleanup callbacks idempotent and bounded.",
                    ],
                },
            )
        finally:
            status = 1 if timed_out else 0
            duration_ms = self._duration_ms(started)
            self._exit_status = status
            self._result = ShutdownResult(
                exit_status=status,
                timed_out=timed_out,
                duration_ms=duration_ms,
                errors=list(self._errors),
            )
            self._complete_event.set()
            self._log_event(
                "shutdown_complete",
                message=f"Shutdown complete, exiting with status {status}",
                exit_status=status,
                timed_out=timed_out,
                duration_ms=duration_ms,
                errors=list(self._errors),
            )

    async def _run_shutdown_steps(self, started: float) -> None:
        await self._run_callback_group(
            "shutdown_callback",
            self._snapshot_callbacks(self._shutdown_callbacks),
            started,
        )
        await self._wait_for_inflight_tasks(started)
        await self._run_callback_group(
            "queue_flush",
            self._snapshot_callbacks(self._queue_flushers),
            started,
        )
        await self._run_callback_group(
            "connection_close",
            self._snapshot_callbacks(self._connection_closers),
            started,
        )

    async def _run_callback_group(
        self,
        phase: str,
        callbacks: list[ShutdownCallback],
        shutdown_started: float,
    ) -> None:
        self._log_event(
            f"{phase}_group_start",
            message=f"{phase} group started",
            callback_count=len(callbacks),
        )
        for index, callback in enumerate(callbacks):
            remaining = self._remaining_timeout(shutdown_started)
            if remaining <= 0:
                raise ShutdownTimeoutError(
                    f"Shutdown timed out before {phase} callback {index}"
                )
            callback_started = time.perf_counter()
            callback_name = self._callback_name(callback)
            try:
                await asyncio.wait_for(
                    self._invoke_callback(callback),
                    timeout=remaining,
                )
                self._log_event(
                    f"{phase}_success",
                    message=f"{phase} callback completed",
                    callback=callback_name,
                    callback_index=index,
                    duration_ms=self._duration_ms(callback_started),
                )
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                self._record_error(f"{phase} callback failed: {exc}")
                self._log_error(
                    exc,
                    {
                        "event_name": f"{phase}_error",
                        "callback": callback_name,
                        "callback_index": index,
                        "duration_ms": self._duration_ms(callback_started),
                        "recovery_suggestions": [
                            "Make shutdown callbacks idempotent.",
                            "Handle cleanup errors inside non-critical callbacks.",
                        ],
                    },
                )
        self._log_event(
            f"{phase}_group_complete",
            message=f"{phase} group completed",
            callback_count=len(callbacks),
        )

    async def _wait_for_inflight_tasks(self, shutdown_started: float) -> None:
        self._log_event(
            "shutdown_wait_inflight_start",
            message="Waiting for in-flight tasks to complete",
            inflight_count=self.inflight_count,
        )
        while self.inflight_count > 0:
            remaining = self._remaining_timeout(shutdown_started)
            if remaining <= 0:
                raise ShutdownTimeoutError("Timed out waiting for in-flight tasks")
            await asyncio.sleep(min(0.05, remaining))
        self._log_event(
            "shutdown_wait_inflight_complete",
            message="In-flight tasks completed",
            inflight_count=0,
        )

    async def _invoke_callback(self, callback: ShutdownCallback) -> Any:
        if inspect.iscoroutinefunction(callback):
            return await callback()
        result = await asyncio.to_thread(callback)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _wait_for_shutdown_async(self) -> None:
        while not self._complete_event.is_set():
            await asyncio.sleep(0.01)

    def _snapshot_callbacks(
        self,
        callbacks: list[ShutdownCallback],
    ) -> list[ShutdownCallback]:
        with self._lock:
            return list(callbacks)

    def _remaining_timeout(self, started: float) -> float:
        return self.timeout_seconds - (time.perf_counter() - started)

    def _record_error(self, message: str) -> None:
        with self._lock:
            self._errors.append(message)

    def _log_event(self, event_name: str, **kwargs: Any) -> None:
        kwargs.setdefault("timestamp_iso", self._utc_timestamp())
        if hasattr(self.logger, "log_event"):
            self.logger.log_event(event_name, **kwargs)
            return
        self.logger.info("%s %s", event_name, kwargs)

    def _log_error(self, error: Exception | str, context: dict[str, Any]) -> None:
        context = dict(context)
        context.setdefault("timestamp_iso", self._utc_timestamp())
        if hasattr(self.logger, "log_error"):
            self.logger.log_error(error, context)
            return
        self.logger.exception("%s %s", error, context)

    @staticmethod
    def _run_coroutine_sync(coro: Awaitable[int]) -> int:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        if hasattr(coro, "close"):
            coro.close()
        raise RuntimeError(
            "shutdown_gracefully() cannot run inside an active event loop; "
            "use await shutdown_gracefully_async() instead"
        )

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _signal_name(signum: int) -> str:
        try:
            return signal.Signals(signum).name
        except ValueError:
            return str(signum)

    @staticmethod
    def _callback_name(callback: ShutdownCallback) -> str:
        return getattr(callback, "__qualname__", getattr(callback, "__name__", repr(callback)))

    @staticmethod
    def _validate_callback(callback: ShutdownCallback) -> None:
        if not callable(callback):
            raise TypeError("shutdown callback must be callable")


class GracefulShutdownHandler:
    """Small facade around ShutdownManager for agent integration code."""

    def __init__(
        self,
        timeout_seconds: float = 30,
        logger: StructuredLogger | logging.Logger | None = None,
        *,
        exit_on_shutdown: bool = False,
    ) -> None:
        self.manager = ShutdownManager(
            timeout_seconds=timeout_seconds,
            logger=logger,
            exit_on_shutdown=exit_on_shutdown,
        )

    def register_shutdown_callback(self, callback: ShutdownCallback) -> None:
        """Register a shutdown callback."""
        self.manager.register_shutdown_callback(callback)

    def register_queue_flusher(self, callback: ShutdownCallback) -> None:
        """Register a queue flush callback."""
        self.manager.register_queue_flusher(callback)

    def register_connection_closer(self, callback: ShutdownCallback) -> None:
        """Register a connection close callback."""
        self.manager.register_connection_closer(callback)

    def start(self) -> "GracefulShutdownHandler":
        """Start signal handling."""
        self.manager.start()
        return self

    def shutdown_gracefully(self) -> int:
        """Start graceful shutdown and return exit status."""
        return self.manager.shutdown_gracefully()

    async def shutdown_gracefully_async(self) -> int:
        """Start graceful shutdown from asyncio code and return exit status."""
        return await self.manager.shutdown_gracefully_async()

    def is_shutting_down(self) -> bool:
        """Return True when shutdown is in progress."""
        return self.manager.is_shutting_down()

    def wait_for_shutdown(self) -> int:
        """Block until shutdown completes."""
        return self.manager.wait_for_shutdown()

    def can_accept_tasks(self) -> bool:
        """Return True when new work can be accepted."""
        return self.manager.can_accept_tasks()

    def track_task(self, task_name: str | None = None):
        """Track a synchronous task with a context manager."""
        return self.manager.track_task(task_name)

    def track_async_task(self, task_name: str | None = None):
        """Track an async task with an async context manager."""
        return self.manager.track_async_task(task_name)

    @property
    def result(self) -> ShutdownResult | None:
        """Return shutdown result once complete."""
        return self.manager.result

    def __enter__(self) -> "GracefulShutdownHandler":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.shutdown_gracefully()

    async def __aenter__(self) -> "GracefulShutdownHandler":
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.shutdown_gracefully_async()
