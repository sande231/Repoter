"""
SelfHealingManager - Coordinates all 4 self-healing strategies.

Runs a background loop that:
1. Checks connectivity → reconnects if down (Strategy #2)
2. Checks queue depth → drains backlog (Strategy #4)
3. Checks health status → restarts if degraded too long (Strategy #1)

Strategy #3 (ConfigReloadHealer) is used inline by wrapping operations.
"""

import asyncio
from self_healing_strategy_1 import AutoRestartHealer
from self_healing_strategy_2 import AutoReconnectHealer
from self_healing_strategy_3 import ConfigReloadHealer
from self_healing_strategy_4 import QueueHealer


class SelfHealingManager:
    """Runs all self-healing strategies for an agent."""

    def __init__(
        self,
        agent,
        check_interval_seconds: int = 60,
        degraded_threshold_seconds: int = 300,
        queue_backlog_threshold: int = 100,
    ):
        self.agent = agent
        self.logger = agent.logger
        self.check_interval_seconds = check_interval_seconds

        # Initialize all healers
        self.restart_healer = AutoRestartHealer(
            agent, degraded_threshold_seconds=degraded_threshold_seconds
        )
        self.reconnect_healer = AutoReconnectHealer(agent)
        self.config_healer = ConfigReloadHealer(agent)
        self.queue_healer = QueueHealer(
            agent, backlog_threshold=queue_backlog_threshold
        )

        self._running = False

    async def run_healing_cycle(self) -> dict:
        """Run one complete healing cycle. Returns summary of actions taken."""
        summary = {"reconnected": False, "queue_healed": False, "restarted": False}

        # 1. Check connectivity first (other healers need the server)
        connected = await self.reconnect_healer.check_connection()
        if not connected:
            self.logger.log_event("healing_cycle_server_down", agent_id=self.agent.agent_id)
            summary["reconnected"] = await self.reconnect_healer.heal()
            if not summary["reconnected"]:
                # Server still down — skip queue healing, but still check restart
                summary["restarted"] = await self.restart_healer.check_and_heal()
                return summary

        # 2. Drain queue backlog if needed (server is up)
        queue_result = await self.queue_healer.check_and_heal()
        summary["queue_healed"] = queue_result.get("healed", False)

        # 3. Restart if degraded too long
        summary["restarted"] = await self.restart_healer.check_and_heal()

        return summary

    async def run_forever(self):
        """Background loop — call this with asyncio.create_task()."""
        self._running = True
        self.logger.log_event(
            "self_healing_manager_started",
            agent_id=self.agent.agent_id,
            check_interval=self.check_interval_seconds,
        )

        try:
            while self._running:
                if hasattr(self.agent, "is_shutting_down") and self.agent.is_shutting_down():
                    break
                try:
                    await self.run_healing_cycle()
                except Exception as e:
                    self.logger.log_error(e, {"action": "healing_cycle"})
                await asyncio.sleep(self.check_interval_seconds)
        finally:
            self.logger.log_event("self_healing_manager_stopped", agent_id=self.agent.agent_id)

    def stop(self):
        self._running = False

    async def execute_with_config_healing(self, operation, operation_name, *args, **kwargs):
        """Wrap any operation with Strategy #3 (config reload on error)."""
        return await self.config_healer.execute_with_healing(
            operation, operation_name, *args, **kwargs
        )