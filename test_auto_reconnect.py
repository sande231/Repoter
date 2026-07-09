"""Test auto-reconnect healer with a mock agent and mock SDK."""

import asyncio
from structured_logger import StructuredLogger
from agent_health_monitor import AgentHealthMonitor
from self_healing_strategy_2 import AutoReconnectHealer


class MockSDK:
    """Mock SDK that simulates a server coming back online."""

    def __init__(self):
        self.ingestion_url = "http://localhost:5000"
        self.local_queue = None  # keep simple for this test


class MockAgent:
    def __init__(self, agent_id="reconnect_test_agent"):
        self.agent_id = agent_id
        self.logger = StructuredLogger(agent_id=agent_id)
        self.health_monitor = AgentHealthMonitor(agent_id=agent_id)
        self.sdk = MockSDK()


async def main():
    print("=" * 60)
    print("TEST: Auto-Reconnect Healer")
    print("=" * 60)

    agent = MockAgent()
    # Short delays so the test finishes quickly
    healer = AutoReconnectHealer(
        agent,
        max_attempts=3,
        base_delay_seconds=1.0,
        max_delay_seconds=4.0,
    )

    print("\nAttempting to reconnect to http://localhost:5000 ...")
    print("(If your ingestion server is RUNNING, this should succeed on attempt 1)")
    print("(If it's STOPPED, you'll see 3 backoff attempts, then exhaustion)\n")

    result = await healer.heal()

    print("\n" + "=" * 60)
    if result:
        print("✅ Reconnection SUCCEEDED — server is reachable")
    else:
        print("⚠️  Reconnection EXHAUSTED — server unreachable (expected if server is off)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())