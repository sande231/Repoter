import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent_config import AgentConfig
from agent_lifecycle import Agent, AgentState, LifecycleManager, LifecycleResult


class CapturingStructuredLogger:
    def __init__(self):
        self.events = []
        self.errors = []

    def log_event(self, event_name, **kwargs):
        self.events.append({"event_name": event_name, **kwargs})

    def log_error(self, error, context):
        self.errors.append({"error": str(error), "context": context})


class RecordingAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.error_context = None

    async def on_startup(self):
        self.calls.append("startup")

    async def on_shutdown(self):
        self.calls.append("shutdown")

    async def on_health_check_fail(self):
        self.calls.append("health_check_fail")

    async def on_config_update(self, new_config):
        self.calls.append("config_update")
        self.config = new_config

    async def on_error(self, exception, context):
        self.calls.append("error")
        self.error_context = {"exception": str(exception), "context": context}


def test_agent_startup_logs_and_updates_status():
    logger = CapturingStructuredLogger()
    agent = RecordingAgent(agent_id="agent-1", logger=logger)

    result = asyncio.run(agent.start())

    assert isinstance(result, LifecycleResult)
    assert result.success is True
    assert result.event_name == "on_startup"
    assert agent.state == AgentState.RUNNING
    assert agent.started_at is not None
    assert agent.calls == ["startup"]
    assert [event["event_name"] for event in logger.events] == [
        "lifecycle_start",
        "lifecycle_success",
    ]


def test_agent_config_update_applies_new_config():
    logger = CapturingStructuredLogger()
    agent = RecordingAgent(agent_id="agent-1", logger=logger)

    result = asyncio.run(
        agent.apply_config_update({"telemetry_batch_size": 10, "log_level": "debug"})
    )

    assert result.success is True
    assert agent.config.telemetry_batch_size == 10
    assert agent.config.log_level == "DEBUG"
    assert agent.calls == ["config_update"]


def test_agent_health_check_fail_hook_marks_degraded_on_success():
    agent = RecordingAgent(agent_id="agent-1", logger=CapturingStructuredLogger())

    result = asyncio.run(agent.health_check_failed())

    assert result.success is True
    assert agent.state == AgentState.DEGRADED
    assert agent.calls == ["health_check_fail"]


def test_agent_handle_error_calls_error_hook_and_records_context():
    logger = CapturingStructuredLogger()
    agent = RecordingAgent(agent_id="agent-1", logger=logger)

    result = asyncio.run(
        agent.handle_error(RuntimeError("boom"), {"task_id": "task-1"})
    )

    assert result.success is True
    assert agent.state == AgentState.DEGRADED
    assert agent.last_error == "boom"
    assert agent.error_context == {
        "exception": "boom",
        "context": {"task_id": "task-1"},
    }


def test_agent_lifecycle_exception_is_captured_with_recovery_suggestions():
    logger = CapturingStructuredLogger()

    class FailingAgent(Agent):
        async def on_startup(self):
            raise RuntimeError("database unavailable")

    agent = FailingAgent(agent_id="agent-1", logger=logger)

    result = asyncio.run(agent.start())

    assert result.success is False
    assert result.state == AgentState.ERROR
    assert result.error_message == "database unavailable"
    assert result.recovery_suggestions
    assert agent.last_error == "database unavailable"
    assert logger.errors[0]["context"]["event_name"] == "lifecycle_error"


def test_agent_shutdown_timeout_is_captured():
    logger = CapturingStructuredLogger()

    class SlowShutdownAgent(Agent):
        async def on_shutdown(self):
            await asyncio.sleep(0.05)

    agent = SlowShutdownAgent(
        agent_id="agent-1",
        logger=logger,
        lifecycle_timeout_seconds=0.01,
    )

    result = asyncio.run(agent.shutdown())

    assert result.success is False
    assert result.state == AgentState.ERROR
    assert "timed out" in result.error_message
    assert logger.errors[0]["context"]["event_name"] == "lifecycle_timeout"


def test_lifecycle_manager_starts_agents_concurrently():
    async def scenario():
        logger = CapturingStructuredLogger()
        manager = LifecycleManager(logger=logger)
        gate = asyncio.Event()
        started = []

        class BarrierAgent(Agent):
            async def on_startup(self):
                started.append(self.agent_id)
                if len(started) == 2:
                    gate.set()
                await gate.wait()

        manager.register_agent(
            BarrierAgent(agent_id="agent-1", logger=CapturingStructuredLogger())
        )
        manager.register_agent(
            BarrierAgent(agent_id="agent-2", logger=CapturingStructuredLogger())
        )

        results = await manager.start_all_agents()

        assert sorted(started) == ["agent-1", "agent-2"]
        assert set(results) == {"agent-1", "agent-2"}
        assert all(result.success for result in results.values())
        assert logger.events[0]["event_name"] == "agent_registered"

    asyncio.run(scenario())


def test_lifecycle_manager_stops_agents_and_runs_shutdown_hooks():
    async def scenario():
        hook_calls = []
        manager = LifecycleManager(logger=CapturingStructuredLogger())
        agent = RecordingAgent(
            agent_id="agent-1",
            logger=CapturingStructuredLogger(),
            config=AgentConfig(),
        )
        manager.register_agent(agent)

        async def shutdown_hook():
            hook_calls.append("hook")

        manager.add_shutdown_hook(shutdown_hook)
        results = await manager.stop_all_agents()

        assert results["agent-1"].success is True
        assert agent.calls == ["shutdown"]
        assert hook_calls == ["hook"]
        assert agent.state == AgentState.STOPPED

    asyncio.run(scenario())


def test_lifecycle_manager_status_and_registration_validation():
    manager = LifecycleManager(logger=CapturingStructuredLogger())
    agent = RecordingAgent(agent_id="agent-1", logger=CapturingStructuredLogger())

    manager.register_agent(agent)

    assert manager.get_agent_status("agent-1")["state"] == AgentState.INITIALIZED
    assert manager.get_agent_status("missing") is None
    with pytest.raises(ValueError, match="already registered"):
        manager.register_agent(agent)
    with pytest.raises(TypeError, match="Agent instance"):
        manager.register_agent(object())
