import asyncio
import os
import signal
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from graceful_shutdown import GracefulShutdownHandler, ShutdownManager, ShutdownResult


class CapturingLogger:
    def __init__(self):
        self.events = []
        self.errors = []

    def log_event(self, event_name, **kwargs):
        self.events.append({"event_name": event_name, **kwargs})

    def log_error(self, error, context):
        self.errors.append({"error": str(error), "context": context})


def _event_names(logger):
    return [event["event_name"] for event in logger.events]


def test_start_registers_sigterm_and_sigint(monkeypatch):
    logger = CapturingLogger()
    registered = {}

    monkeypatch.setattr("graceful_shutdown.signal.getsignal", lambda sig: "old")
    monkeypatch.setattr(
        "graceful_shutdown.signal.signal",
        lambda sig, handler: registered.setdefault(sig, handler),
    )

    manager = ShutdownManager(logger=logger)

    assert manager.start() is manager
    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered
    assert "shutdown_signal_handlers_started" in _event_names(logger)


def test_signal_handler_starts_graceful_shutdown(monkeypatch):
    logger = CapturingLogger()
    registered = {}

    monkeypatch.setattr("graceful_shutdown.signal.getsignal", lambda sig: "old")
    monkeypatch.setattr(
        "graceful_shutdown.signal.signal",
        lambda sig, handler: registered.setdefault(sig, handler),
    )
    manager = ShutdownManager(timeout_seconds=1, logger=logger)
    manager.start()

    registered[signal.SIGTERM](signal.SIGTERM, None)
    status = manager.wait_for_shutdown()

    assert status == 0
    assert manager.result is not None
    assert manager.result.exit_status == 0
    assert manager.is_shutting_down() is True
    assert "shutdown_signal_received" in _event_names(logger)
    assert "shutdown_complete" in _event_names(logger)


def test_shutdown_sequence_runs_callbacks_waits_tasks_flushes_and_closes():
    async def scenario():
        logger = CapturingLogger()
        manager = ShutdownManager(timeout_seconds=1, logger=logger)
        order = []

        async def shutdown_callback():
            order.append("callback")

        def flush_queue():
            order.append("flush")

        def close_connection():
            order.append("close")

        manager.register_shutdown_callback(shutdown_callback)
        manager.register_queue_flusher(flush_queue)
        manager.register_connection_closer(close_connection)
        assert manager.task_started("active-task") is True

        async def finish_task():
            await asyncio.sleep(0.05)
            order.append("task_done")
            manager.task_finished("active-task")

        finisher = asyncio.create_task(finish_task())
        shutdown_task = asyncio.create_task(manager.shutdown_gracefully_async())
        await asyncio.sleep(0)

        assert manager.is_shutting_down() is True
        assert manager.can_accept_tasks() is False
        status = await shutdown_task
        await finisher

        assert status == 0
        assert order == ["callback", "task_done", "flush", "close"]
        assert isinstance(manager.result, ShutdownResult)
        assert manager.result.exit_status == 0
        assert manager.result.timed_out is False

    asyncio.run(scenario())


def test_new_tasks_are_rejected_after_shutdown_starts():
    async def scenario():
        logger = CapturingLogger()
        manager = ShutdownManager(timeout_seconds=1, logger=logger)
        shutdown_task = asyncio.create_task(manager.shutdown_gracefully_async())
        await asyncio.sleep(0)

        assert manager.can_accept_tasks() is False
        assert manager.task_started("too-late") is False

        assert await shutdown_task == 0
        assert "shutdown_task_rejected" in _event_names(logger)

    asyncio.run(scenario())


def test_task_context_managers_track_sync_and_async_work():
    async def scenario():
        manager = ShutdownManager(logger=CapturingLogger())

        with manager.track_task("sync-task") as started:
            assert started is True
            assert manager.inflight_count == 1
        assert manager.inflight_count == 0

        async with manager.track_async_task("async-task") as async_started:
            assert async_started is True
            assert manager.inflight_count == 1
        assert manager.inflight_count == 0

    asyncio.run(scenario())


def test_multiple_shutdown_requests_do_not_rerun_callbacks():
    async def scenario():
        logger = CapturingLogger()
        manager = ShutdownManager(timeout_seconds=1, logger=logger)
        calls = []

        async def slow_callback():
            calls.append("called")
            await asyncio.sleep(0.05)

        manager.register_shutdown_callback(slow_callback)
        results = await asyncio.gather(
            manager.shutdown_gracefully_async(),
            manager.shutdown_gracefully_async(),
        )

        assert results == [0, 0]
        assert calls == ["called"]
        assert "shutdown_already_in_progress" in _event_names(logger)

    asyncio.run(scenario())


def test_callback_exception_is_logged_and_shutdown_continues():
    async def scenario():
        logger = CapturingLogger()
        manager = ShutdownManager(timeout_seconds=1, logger=logger)
        order = []

        def broken_callback():
            order.append("broken")
            raise RuntimeError("cleanup failed")

        def flush_queue():
            order.append("flush")

        def close_connection():
            order.append("close")

        manager.register_shutdown_callback(broken_callback)
        manager.register_queue_flusher(flush_queue)
        manager.register_connection_closer(close_connection)

        status = await manager.shutdown_gracefully_async()

        assert status == 0
        assert order == ["broken", "flush", "close"]
        assert manager.result is not None
        assert manager.result.errors == [
            "shutdown_callback callback failed: cleanup failed"
        ]
        assert logger.errors[0]["context"]["event_name"] == "shutdown_callback_error"

    asyncio.run(scenario())


def test_timeout_returns_status_one():
    async def scenario():
        logger = CapturingLogger()
        manager = ShutdownManager(timeout_seconds=0.05, logger=logger)
        assert manager.task_started("stuck-task") is True

        status = await manager.shutdown_gracefully_async()

        assert status == 1
        assert manager.result is not None
        assert manager.result.exit_status == 1
        assert manager.result.timed_out is True
        assert "Graceful shutdown timed out" in manager.result.errors
        assert logger.errors[0]["context"]["event_name"] == "shutdown_timeout"

    asyncio.run(scenario())


def test_context_manager_starts_and_stops(monkeypatch):
    logger = CapturingLogger()
    registered = {}

    monkeypatch.setattr("graceful_shutdown.signal.getsignal", lambda sig: "old")
    monkeypatch.setattr(
        "graceful_shutdown.signal.signal",
        lambda sig, handler: registered.setdefault(sig, handler),
    )

    with ShutdownManager(timeout_seconds=1, logger=logger) as manager:
        assert signal.SIGTERM in registered
        assert manager.can_accept_tasks() is True

    assert manager.result is not None
    assert manager.result.exit_status == 0


def test_async_context_manager_starts_and_stops(monkeypatch):
    async def scenario():
        logger = CapturingLogger()
        registered = {}

        monkeypatch.setattr("graceful_shutdown.signal.getsignal", lambda sig: "old")
        monkeypatch.setattr(
            "graceful_shutdown.signal.signal",
            lambda sig, handler: registered.setdefault(sig, handler),
        )

        async with ShutdownManager(timeout_seconds=1, logger=logger) as manager:
            assert signal.SIGINT in registered
            assert manager.can_accept_tasks() is True

        assert manager.result is not None
        assert manager.result.exit_status == 0

    asyncio.run(scenario())


def test_graceful_shutdown_handler_delegates_to_manager():
    async def scenario():
        logger = CapturingLogger()
        handler = GracefulShutdownHandler(timeout_seconds=1, logger=logger)
        calls = []

        handler.register_shutdown_callback(lambda: calls.append("callback"))
        handler.register_queue_flusher(lambda: calls.append("flush"))
        handler.register_connection_closer(lambda: calls.append("close"))

        with handler.track_task("job") as started:
            assert started is True

        status = await handler.shutdown_gracefully_async()

        assert status == 0
        assert handler.is_shutting_down() is True
        assert handler.can_accept_tasks() is False
        assert calls == ["callback", "flush", "close"]
        assert handler.result is not None

    asyncio.run(scenario())


def test_wait_for_shutdown_blocks_until_background_shutdown_finishes():
    logger = CapturingLogger()
    manager = ShutdownManager(timeout_seconds=1, logger=logger)
    calls = []

    def slow_callback():
        time.sleep(0.05)
        calls.append("done")

    manager.register_shutdown_callback(slow_callback)
    thread = threading.Thread(target=manager.shutdown_gracefully)
    thread.start()

    assert manager.wait_for_shutdown() == 0
    thread.join(timeout=1)
    assert calls == ["done"]


def test_registering_non_callable_callback_raises():
    manager = ShutdownManager(logger=CapturingLogger())

    with pytest.raises(TypeError, match="callable"):
        manager.register_shutdown_callback("not-callable")
