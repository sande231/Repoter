# Create a comprehensive test script that validates the HealthCheck dataclass and AgentHealthMonitor class.
#
# SETUP:
# - Import HealthCheck from health_check
# - Import AgentHealthMonitor from agent_health_monitor
# - Create helper functions for colored output (✅, ❌)
#
# TESTS TO RUN:
#
# 1. HEALTHCHECK DATACLASS TESTS:
#    - Verify HealthCheck can be instantiated with all required fields
#    - Verify it has status, uptime_seconds, last_task, error_rate_percent, dependencies, metrics
#    - Test to_json() returns valid JSON string
#    - Test to_dict() returns dictionary
#    - Test is_healthy() returns True when status='healthy'
#    - Test is_degraded() returns True when status='degraded'
#    - Test is_unhealthy() returns True when status='unhealthy'
#    - Verify dataclass has proper type hints
#
# 2. AGENT HEALTH MONITOR INITIALIZATION:
#    - Verify AgentHealthMonitor can be instantiated with agent_id
#    - Verify it initializes with healthy status
#    - Verify uptime_seconds starts at 0 or close to 0
#
# 3. HEARTBEAT TRACKING:
#    - Record a heartbeat
#    - Verify last_task timestamp updates
#    - Record multiple heartbeats
#    - Verify uptime increases over time
#
# 4. ERROR RATE TRACKING:
#    - Record 10 tasks, 1 error
#    - Verify error_rate_percent is ~10%
#    - Record 100 tasks, 5 errors
#    - Verify error_rate_percent is ~5%
#    - Record 100 tasks, 25 errors
#    - Verify error_rate_percent is ~25%
#
# 5. STATUS FLAGS:
#    - Verify agent is healthy when error_rate < 5%
#    - Verify agent is degraded when error_rate between 5-20%
#    - Verify agent is unhealthy when error_rate > 20%
#    - Verify agent degrades when no heartbeat for 2+ minutes
#    - Verify agent unhealthy when no heartbeat for 5+ minutes
#
# 6. DEPENDENCY TRACKING:
#    - Set dependency "database" to healthy
#    - Verify get_health_check() shows database: healthy
#    - Set dependency "database" to degraded
#    - Verify agent becomes degraded (even if error_rate is 0%)
#    - Set dependency "database" to unhealthy
#    - Verify agent becomes unhealthy
#
# 7. METRICS COLLECTION:
#    - Set metrics with memory_mb, cpu_percent, queue_depth
#    - Verify get_health_check() includes all metrics
#    - Verify metrics are returned in to_dict() and to_json()
#
# 8. GET_HEALTH_CHECK OUTPUT:
#    - Call get_health_check()
#    - Verify it returns HealthCheck object
#    - Verify all fields are populated
#    - Verify JSON serialization works
#
# 9. RESET METRICS:
#    - Record some data
#    - Call reset_metrics()
#    - Verify error_rate_percent resets to 0
#    - Verify task counts reset
#
# 10. INTEGRATION TEST:
#     - Create multiple agents
#     - Have them report different health statuses
#     - Verify each has correct status
#     - Serialize to JSON and verify format
#
# OUTPUT:
# - Print progress with ✅ PASSED or ❌ FAILED for each test
# - Count total tests passed/failed
# - Print final summary with color coding
# - Exit with code 0 if all pass, 1 if any fail
#
# Use try-except throughout for robust error handling.
# Make output clear and easy to read with emojis and formatting.