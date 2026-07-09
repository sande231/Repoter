"""Test config-reload healer with a mock agent."""

import asyncio
from structured_logger import StructuredLogger
from agent_health_monitor import AgentHealthMonitor
from agent_config import AgentConfig, ConfigManager
from self_healing_strategy_3 import ConfigReloadHealer


class MockAgent:
    def __init__(self, agent_id="config_test_agent"):
        self.agent_id = agent_id
        self.logger = StructuredLogger(agent_id=agent_id)
        self.health_monitor = AgentHealthMonitor(agent_id=agent_id)
        self.config_manager = ConfigManager()
        self.config = self.config_manager.get_config(agent_id)


# --- Simulated operations ---

call_count = {"flaky": 0}

def operation_that_succeeds():
    """Always works."""
    return "success!"

def operation_that_always_fails():
    """Always fails, even after config reload."""
    raise ConnectionError("Simulated permanent failure")

def flaky_operation():
    """Fails the FIRST time, succeeds after config reload (2nd call)."""
    call_count["flaky"] += 1
    if call_count["flaky"] == 1:
        raise TimeoutError("Simulated stale-config timeout")
    return "recovered after config reload!"


async def main():
    print("=" * 60)
    print("TEST: Config Reload Healer")
    print("=" * 60)

    agent = MockAgent()
    healer = ConfigReloadHealer(agent)
    passed = 0
    failed = 0

    # Test 1: Normal operation (no healing needed)
    print("\n1. Testing operation that succeeds normally...")
    ok, result = await healer.execute_with_healing(
        operation_that_succeeds, "normal_op"
    )
    if ok and result == "success!":
        print("   ✅ PASSED — succeeded without healing")
        passed += 1
    else:
        print(f"   ❌ FAILED — ok={ok}, result={result}")
        failed += 1

    # Test 2: Flaky operation (fails once, healed by config reload + retry)
    print("\n2. Testing flaky operation (should heal via config reload)...")
    ok, result = await healer.execute_with_healing(
        flaky_operation, "flaky_op"
    )
    if ok and "recovered" in str(result):
        print(f"   ✅ PASSED — healed: {result}")
        passed += 1
    else:
        print(f"   ❌ FAILED — ok={ok}, result={result}")
        failed += 1

    # Test 3: Permanent failure (healing attempted, but operation still fails)
    print("\n3. Testing permanently failing operation...")
    ok, result = await healer.execute_with_healing(
        operation_that_always_fails, "permanent_fail_op"
    )
    if not ok and isinstance(result, ConnectionError):
        print("   ✅ PASSED — failed gracefully after reload+retry (as expected)")
        passed += 1
    else:
        print(f"   ❌ FAILED — ok={ok}, result={result}")
        failed += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"Config reloads performed: {healer.reload_count}")
    if failed == 0:
        print(f"✅ ALL {passed} TESTS PASSED!")
    else:
        print(f"❌ {failed} test(s) failed, {passed} passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())