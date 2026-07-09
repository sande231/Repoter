"""Test auto-restart healer with a self-contained mock agent."""

import asyncio
from structured_logger import StructuredLogger
from agent_health_monitor import AgentHealthMonitor
from self_healing_strategy_1 import AutoRestartHealer


class MockAgent:
    """Minimal agent for testing the healer without external dependencies."""

    def __init__(self, agent_id="mock_agent"):
        self.agent_id = agent_id
        self.logger = StructuredLogger(agent_id=agent_id)
        self.health_monitor = AgentHealthMonitor(agent_id=agent_id)
        self._shutting_down = False
        self.restart_count = 0

    async def on_startup(self):
        self.logger.log_event("mock_agent_startup")
        # Reset health after restart so agent becomes healthy again
        self.health_monitor.reset_metrics()
        self.restart_count += 1

    async def on_shutdown(self):
        self.logger.log_event("mock_agent_shutdown")

    def get_health_check(self):
        return self.health_monitor.get_health_check()

    def is_shutting_down(self):
        return self._shutting_down


async def main():
    print("=" * 60)
    print("TEST: Auto-Restart Healer")
    print("=" * 60)

    # Create mock agent
    agent = MockAgent()
    await agent.on_startup()
    initial_restarts = agent.restart_count

    # Create healer with a SHORT threshold for testing (10 seconds)
    healer = AutoRestartHealer(agent, degraded_threshold_seconds=10)

    # Step 1: Simulate a degraded agent (high error rate)
    print("\n1. Simulating degraded agent (recording errors)...")
    for i in range(20):
        agent.health_monitor.record_error()
    for i in range(80):
        agent.health_monitor.record_task_completion()

    health = agent.get_health_check()
    print(f"   Health status: {health.status} (error rate: {health.error_rate_percent}%)")

    # Step 2: First check — should START tracking degraded, not restart yet
    print("\n2. First health check (starts degraded timer)...")
    healed = await healer.check_and_heal()
    print(f"   Healed: {healed} (expected: False — timer just started)")

    # Step 3: Wait past the threshold
    print("\n3. Waiting 12 seconds (past the 10s threshold)...")
    await asyncio.sleep(12)

    # Step 4: Second check — should trigger restart
    print("\n4. Second health check (should trigger auto-restart)...")
    healed = await healer.check_and_heal()
    print(f"   Healed: {healed} (expected: True)")

    # Step 5: Verify
    print("\n" + "=" * 60)
    restarted = agent.restart_count > initial_restarts
    health_after = agent.get_health_check()

    if healed and restarted:
        print("✅ TEST PASSED: Auto-restart triggered and completed!")
        print(f"   Restart count: {agent.restart_count}")
        print(f"   Health after restart: {health_after.status}")
    else:
        print("❌ TEST FAILED")
        print(f"   healed={healed}, restart_count={agent.restart_count}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())