"""
Universal Healing Wrapper - Add self-healing to ANY agent.

Usage:
    from healing_wrapper import make_healable, run_with_healing

    agent = make_healable(MyAnyAgent(), agent_id="my-agent")
    asyncio.run(run_with_healing(agent, heartbeat_fn=agent.wrapped.publish_heartbeat))
"""

import asyncio
from structured_logger import StructuredLogger
from agent_health_monitor import AgentHealthMonitor
from agent_config import ConfigManager
from self_healing_manager import SelfHealingManager


class HealableAgent:
    """Universal wrapper that makes any agent compatible with SelfHealingManager."""

    def __init__(self, wrapped_agent, agent_id: str, sdk=None):
        """
        Args:
            wrapped_agent: ANY agent object (CanvasTutorAdapter, future agents, etc.)
            agent_id: Unique agent identifier
            sdk: The agent's SDK (auto-detected from wrapped_agent.sdk if not given)
        """
        self.wrapped = wrapped_agent
        self.agent_id = agent_id
        self.sdk = sdk or getattr(wrapped_agent, "sdk", None)

        # Standard healing infrastructure — same for every agent
        self.logger = StructuredLogger(agent_id=agent_id)
        self.health_monitor = AgentHealthMonitor(agent_id=agent_id)
        self.config_manager = ConfigManager()
        self.config = self.config_manager.get_config(agent_id)
        self._shutting_down = False

    def is_shutting_down(self):
        return self._shutting_down

    def get_health_check(self):
        return self.health_monitor.get_health_check()

    async def on_startup(self):
        self.logger.log_event("healable_agent_startup")
        self.health_monitor.reset_metrics()
        # Call the wrapped agent's startup/register if it has one
        for method_name in ("on_startup", "register", "start"):
            method = getattr(self.wrapped, method_name, None)
            if method:
                if asyncio.iscoroutinefunction(method):
                    await method()
                else:
                    method()
                break

    async def on_shutdown(self):
        self.logger.log_event("healable_agent_shutdown")
        for method_name in ("on_shutdown", "stop", "close"):
            method = getattr(self.wrapped, method_name, None)
            if method:
                if asyncio.iscoroutinefunction(method):
                    await method()
                else:
                    method()
                break

    def record_success(self):
        self.health_monitor.record_task_completion()

    def record_failure(self):
        self.health_monitor.record_error()


def make_healable(agent, agent_id: str, sdk=None) -> HealableAgent:
    """Wrap any agent to make it self-healing compatible."""
    return HealableAgent(agent, agent_id=agent_id, sdk=sdk)


async def run_with_healing(
    healable: HealableAgent,
    work_fn,
    work_interval_seconds: int = 10,
    healing_check_interval: int = 15,
    degraded_threshold: int = 60,
    queue_threshold: int = 5,
):
    """
    Run any agent's work loop with self-healing in the background.

    Args:
        healable: A HealableAgent (from make_healable)
        work_fn: The function to call each cycle (e.g., publish_heartbeat)
        work_interval_seconds: Seconds between work cycles
    """
    await healable.on_startup()

    healing = SelfHealingManager(
        healable,
        check_interval_seconds=healing_check_interval,
        degraded_threshold_seconds=degraded_threshold,
        queue_backlog_threshold=queue_threshold,
    )
    healing_task = asyncio.create_task(healing.run_forever())

    try:
        while not healable.is_shutting_down():
            try:
                work_fn()
                # Track success/failure via SDK error state if available
                if healable.sdk and getattr(healable.sdk, "last_error", None):
                    healable.record_failure()
                else:
                    healable.record_success()
            except Exception as e:
                healable.logger.log_error(e, {"action": "work_cycle"})
                healable.record_failure()

            await asyncio.sleep(work_interval_seconds)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        healable._shutting_down = True
        healing.stop()
        healing_task.cancel()
        await healable.on_shutdown()