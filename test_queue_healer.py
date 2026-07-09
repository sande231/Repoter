"""Test queue healer with a mock queue and mock SDK."""

import asyncio
from structured_logger import StructuredLogger
from agent_health_monitor import AgentHealthMonitor
from self_healing_strategy_4 import QueueHealer


class MockQueue:
    """In-memory mock of LocalQueue."""

    def __init__(self, items):
        self.items = list(items)

    def get_pending_count(self):
        return len(self.items)

    def dequeue(self):
        if self.items:
            return self.items.pop(0)
        return None


class MockSDK:
    """Mock SDK. Set server_up=False to simulate outage mid-drain."""

    def __init__(self, queue):
        self.local_queue = queue
        self.server_up = True
        self.sent = 0

    def send_metrics(self, item):
        if not self.server_up:
            return None  # Simulates connection failure
        self.sent += 1
        return {"status": "ok"}


class MockAgent:
    def __init__(self, queue, agent_id="queue_test_agent"):
        self.agent_id = agent_id
        self.logger = StructuredLogger(agent_id=agent_id)
        self.health_monitor = AgentHealthMonitor(agent_id=agent_id)
        self.sdk = MockSDK(queue)


async def main():
    print("=" * 60)
    print("TEST: Queue Self-Healing")
    print("=" * 60)
    passed = 0
    failed = 0

    # Test 1: Small queue — below threshold, no healing needed
    print("\n1. Small queue (10 items, threshold 100) — should NOT heal...")
    agent = MockAgent(MockQueue([{"metric": i} for i in range(10)]))
    healer = QueueHealer(agent, backlog_threshold=100)
    result = await healer.check_and_heal()
    if not result["healed"] and result["remaining"] == 10:
        print("   ✅ PASSED — queue below threshold, left alone")
        passed += 1
    else:
        print(f"   ❌ FAILED — {result}")
        failed += 1

    # Test 2: Big backlog — should drain completely
    print("\n2. Big backlog (250 items, threshold 100) — should drain all...")
    agent = MockAgent(MockQueue([{"metric": i} for i in range(250)]))
    healer = QueueHealer(agent, backlog_threshold=100, batch_size=50, batch_pause_seconds=0.1)
    result = await healer.check_and_heal()
    if result["healed"] and result["flushed"] == 250 and result["remaining"] == 0:
        print(f"   ✅ PASSED — flushed all {result['flushed']} items")
        passed += 1
    else:
        print(f"   ❌ FAILED — {result}")
        failed += 1

    # Test 3: Server dies mid-drain — should stop safely, keep remaining items
    print("\n3. Server dies mid-drain — should stop safely...")
    queue = MockQueue([{"metric": i} for i in range(200)])
    agent = MockAgent(queue)
    healer = QueueHealer(agent, backlog_threshold=100, batch_size=50, batch_pause_seconds=0.1)

    # Kill the server after 75 items are sent
    original_send = agent.sdk.send_metrics
    def send_then_die(item):
        if agent.sdk.sent >= 75:
            agent.sdk.server_up = False
        return original_send(item)
    agent.sdk.send_metrics = send_then_die

    result = await healer.check_and_heal()
    if not result["healed"] and result["flushed"] == 75 and result["remaining"] > 0:
        print(f"   ✅ PASSED — stopped at {result['flushed']}, {result['remaining']} items preserved")
        passed += 1
    else:
        print(f"   ❌ FAILED — {result}")
        failed += 1

    # Summary
    print("\n" + "=" * 60)
    if failed == 0:
        print(f"✅ ALL {passed} TESTS PASSED!")
    else:
        print(f"❌ {failed} test(s) failed, {passed} passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())