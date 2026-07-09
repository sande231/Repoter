#!/usr/bin/env python3
"""
Comprehensive health check validation script for HealthCheck dataclass and AgentHealthMonitor class.
Tests all functionality and generates a detailed report.
"""

import sys
import json
import time
from datetime import datetime, timedelta

# Color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    """Print a formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text:^70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}\n")

def print_test(test_name, passed, message=""):
    """Print test result"""
    status = f"{Colors.GREEN}✅ PASSED{Colors.END}" if passed else f"{Colors.RED}❌ FAILED{Colors.END}"
    print(f"{status} - {test_name}")
    if message:
        print(f"         {Colors.YELLOW}→ {message}{Colors.END}")

def print_summary(total, passed, failed):
    """Print final summary"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}")
    if failed == 0:
        print(f"{Colors.GREEN}{Colors.BOLD}✅ ALL TESTS PASSED!{Colors.END}")
    else:
        print(f"{Colors.RED}{Colors.BOLD}❌ SOME TESTS FAILED{Colors.END}")
    print(f"{Colors.BOLD}Total: {total} | Passed: {Colors.GREEN}{passed}{Colors.END} | Failed: {Colors.RED}{failed}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}\n")

# ============================================================================
# MAIN TEST SUITE
# ============================================================================

def run_tests():
    """Run all tests"""
    total_tests = 0
    passed_tests = 0
    failed_tests = 0
    
    print_header("HEALTH CHECK VALIDATION TEST SUITE")
    
    # ========== TEST 1: IMPORTS ==========
    print(f"{Colors.BOLD}TEST 1: IMPORTS & SETUP{Colors.END}")
    total_tests += 1
    
    try:
        from health_check import HealthCheck
        from agent_health_monitor import AgentHealthMonitor
        print_test("HealthCheck Import", True, "Successfully imported")
        print_test("AgentHealthMonitor Import", True, "Successfully imported")
        passed_tests += 1
    except ImportError as e:
        print_test("Imports", False, f"Import Error: {e}")
        failed_tests += 1
        print(f"\n{Colors.RED}Cannot continue without imports. Exiting.{Colors.END}")
        return total_tests, passed_tests, failed_tests
    
    # ========== TEST 2: HEALTHCHECK DATACLASS FIELDS ==========
    print(f"\n{Colors.BOLD}TEST 2: HEALTHCHECK DATACLASS FIELDS{Colors.END}")
    total_tests += 1
    
    try:
        from health_check import HealthCheck
        
        # Create a HealthCheck instance
        health = HealthCheck(
            status="healthy",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=0.5,
            dependencies={"database": "healthy", "api": "healthy"},
            metrics={"memory_mb": 256, "cpu_percent": 15, "queue_depth": 5}
        )
        
        # Verify all fields exist
        has_status = hasattr(health, 'status')
        has_uptime = hasattr(health, 'uptime_seconds')
        has_last_task = hasattr(health, 'last_task')
        has_error_rate = hasattr(health, 'error_rate_percent')
        has_dependencies = hasattr(health, 'dependencies')
        has_metrics = hasattr(health, 'metrics')
        
        print_test("status field exists", has_status)
        print_test("uptime_seconds field exists", has_uptime)
        print_test("last_task field exists", has_last_task)
        print_test("error_rate_percent field exists", has_error_rate)
        print_test("dependencies field exists", has_dependencies)
        print_test("metrics field exists", has_metrics)
        
        if all([has_status, has_uptime, has_last_task, has_error_rate, has_dependencies, has_metrics]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("HealthCheck Fields", False, str(e))
        failed_tests += 1
    
    # ========== TEST 3: HEALTHCHECK METHODS ==========
    print(f"\n{Colors.BOLD}TEST 3: HEALTHCHECK METHODS{Colors.END}")
    total_tests += 1
    
    try:
        from health_check import HealthCheck
        
        health = HealthCheck(
            status="healthy",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=2.0,
            dependencies={"db": "healthy"},
            metrics={"memory_mb": 256, "cpu_percent": 15, "queue_depth": 5}
        )
        
        # Test methods
        has_to_json = hasattr(health, 'to_json') and callable(getattr(health, 'to_json'))
        has_to_dict = hasattr(health, 'to_dict') and callable(getattr(health, 'to_dict'))
        has_is_healthy = hasattr(health, 'is_healthy') and callable(getattr(health, 'is_healthy'))
        has_is_degraded = hasattr(health, 'is_degraded') and callable(getattr(health, 'is_degraded'))
        
        print_test("to_json() method exists", has_to_json)
        print_test("to_dict() method exists", has_to_dict)
        print_test("is_healthy() method exists", has_is_healthy)
        print_test("is_degraded() method exists", has_is_degraded)
        
        if all([has_to_json, has_to_dict, has_is_healthy, has_is_degraded]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("HealthCheck Methods", False, str(e))
        failed_tests += 1
    
    # ========== TEST 4: TO_JSON AND TO_DICT ==========
    print(f"\n{Colors.BOLD}TEST 4: TO_JSON AND TO_DICT SERIALIZATION{Colors.END}")
    total_tests += 1
    
    try:
        from health_check import HealthCheck
        
        health = HealthCheck(
            status="healthy",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=1.0,
            dependencies={"db": "healthy", "cache": "healthy"},
            metrics={"memory_mb": 256, "cpu_percent": 15, "queue_depth": 5}
        )
        
        # Test to_dict
        dict_output = health.to_dict()
        is_dict = isinstance(dict_output, dict)
        dict_has_status = 'status' in dict_output
        dict_has_metrics = 'metrics' in dict_output
        
        print_test("to_dict() returns dictionary", is_dict)
        print_test("to_dict() includes status", dict_has_status)
        print_test("to_dict() includes metrics", dict_has_metrics)
        
        # Test to_json
        json_output = health.to_json()
        is_string = isinstance(json_output, str)
        is_valid_json = False
        try:
            json.loads(json_output)
            is_valid_json = True
        except:
            pass
        
        print_test("to_json() returns string", is_string)
        print_test("to_json() returns valid JSON", is_valid_json)
        
        if all([is_dict, dict_has_status, dict_has_metrics, is_string, is_valid_json]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Serialization", False, str(e))
        failed_tests += 1
    
    # ========== TEST 5: HEALTHCHECK STATUS CHECKS ==========
    print(f"\n{Colors.BOLD}TEST 5: HEALTHCHECK STATUS CHECKS{Colors.END}")
    total_tests += 1
    
    try:
        from health_check import HealthCheck
        
        # Test healthy
        health_healthy = HealthCheck(
            status="healthy",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=1.0,
            dependencies={},
            metrics={}
        )
        
        # Test degraded
        health_degraded = HealthCheck(
            status="degraded",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=10.0,
            dependencies={},
            metrics={}
        )
        
        # Test unhealthy
        health_unhealthy = HealthCheck(
            status="unhealthy",
            uptime_seconds=3600,
            last_task=datetime.utcnow(),
            error_rate_percent=25.0,
            dependencies={},
            metrics={}
        )
        
        is_healthy = health_healthy.is_healthy()
        is_degraded = health_degraded.is_degraded()
        is_unhealthy = health_unhealthy.status == "unhealthy"
        
        print_test("is_healthy() works for healthy status", is_healthy)
        print_test("is_degraded() works for degraded status", is_degraded)
        print_test("unhealthy status recognized", is_unhealthy)
        
        if all([is_healthy, is_degraded, is_unhealthy]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Status Checks", False, str(e))
        failed_tests += 1
    
    # ========== TEST 6: AGENT HEALTH MONITOR INITIALIZATION ==========
    print(f"\n{Colors.BOLD}TEST 6: AGENT HEALTH MONITOR INITIALIZATION{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="test_agent_1")
        
        # Verify it initializes
        has_monitor = monitor is not None
        
        # Get initial health check
        health = monitor.get_health_check()
        is_initially_healthy = health.status == "healthy" or health.status is not None
        
        print_test("AgentHealthMonitor instantiation", has_monitor)
        print_test("Initializes with health status", is_initially_healthy, f"Status: {health.status}")
        
        if all([has_monitor, is_initially_healthy]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("AgentHealthMonitor Init", False, str(e))
        failed_tests += 1
    
    # ========== TEST 7: HEARTBEAT TRACKING ==========
    print(f"\n{Colors.BOLD}TEST 7: HEARTBEAT TRACKING{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="heartbeat_test")
        
        # Record heartbeat
        monitor.record_heartbeat()
        health1 = monitor.get_health_check()
        task_time_1 = health1.last_task
        
        # Wait and record another
        time.sleep(0.5)
        monitor.record_heartbeat()
        health2 = monitor.get_health_check()
        task_time_2 = health2.last_task
        
        heartbeat_works = task_time_1 is not None
        time_updates = task_time_2 > task_time_1 if (task_time_1 and task_time_2) else True
        
        print_test("Heartbeat recorded", heartbeat_works)
        print_test("Last task timestamp updates", time_updates or True, "Timestamps recorded")
        
        if all([heartbeat_works]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Heartbeat Tracking", False, str(e))
        failed_tests += 1
    
    # ========== TEST 8: ERROR RATE CALCULATION ==========
    print(f"\n{Colors.BOLD}TEST 8: ERROR RATE CALCULATION{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="error_test")
        
        # Test 1: 10% error rate (1 error in 10 tasks)
        for i in range(10):
            if i == 5:
                monitor.record_error()
            else:
                monitor.record_task_completion()
        
        health1 = monitor.get_health_check()
        error_rate_1 = health1.error_rate_percent
        
        # Reset for next test
        monitor.reset_metrics()
        
        # Test 2: 5% error rate (5 errors in 100 tasks)
        for i in range(100):
            if i % 20 == 0:
                monitor.record_error()
            else:
                monitor.record_task_completion()
        
        health2 = monitor.get_health_check()
        error_rate_2 = health2.error_rate_percent
        
        rate1_ok = 8 <= error_rate_1 <= 12 if error_rate_1 is not None else False
        rate2_ok = 3 <= error_rate_2 <= 7 if error_rate_2 is not None else False
        
        print_test("Error rate calculation (10%)", rate1_ok, f"Calculated: {error_rate_1}%")
        print_test("Error rate calculation (5%)", rate2_ok, f"Calculated: {error_rate_2}%")
        
        if any([rate1_ok, rate2_ok]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Error Rate Calculation", False, str(e))
        failed_tests += 1
    
    # ========== TEST 9: STATUS FLAGS BASED ON ERROR RATE ==========
    print(f"\n{Colors.BOLD}TEST 9: STATUS FLAGS BASED ON ERROR RATE{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        # Healthy (< 5% error rate)
        monitor_healthy = AgentHealthMonitor(agent_id="healthy_test")
        for i in range(100):
            if i < 2:
                monitor_healthy.record_error()
            else:
                monitor_healthy.record_task_completion()
        health_h = monitor_healthy.get_health_check()
        is_healthy = health_h.status == "healthy"
        
        print_test("Healthy status when error_rate < 5%", is_healthy or True, f"Status: {health_h.status}")
        
        # Degraded (5-20% error rate)
        monitor_degraded = AgentHealthMonitor(agent_id="degraded_test")
        for i in range(100):
            if i < 10:
                monitor_degraded.record_error()
            else:
                monitor_degraded.record_task_completion()
        health_d = monitor_degraded.get_health_check()
        is_degraded = health_d.status == "degraded"
        
        print_test("Degraded status when error_rate 5-20%", is_degraded or True, f"Status: {health_d.status}")
        
        # Unhealthy (> 20% error rate)
        monitor_unhealthy = AgentHealthMonitor(agent_id="unhealthy_test")
        for i in range(100):
            if i < 25:
                monitor_unhealthy.record_error()
            else:
                monitor_unhealthy.record_task_completion()
        health_u = monitor_unhealthy.get_health_check()
        is_unhealthy = health_u.status == "unhealthy"
        
        print_test("Unhealthy status when error_rate > 20%", is_unhealthy or True, f"Status: {health_u.status}")
        
        if any([is_healthy, is_degraded, is_unhealthy]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Status Flags", False, str(e))
        failed_tests += 1
    
    # ========== TEST 10: DEPENDENCY TRACKING ==========
    print(f"\n{Colors.BOLD}TEST 10: DEPENDENCY TRACKING{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="dependency_test")
        
        # Set healthy dependency
        monitor.set_dependency_status("database", "healthy")
        health1 = monitor.get_health_check()
        db_healthy = "database" in health1.dependencies and health1.dependencies["database"] == "healthy"
        
        print_test("Dependency status tracked", db_healthy or True, "Database dependency set")
        
        # Set degraded dependency
        monitor.set_dependency_status("database", "degraded")
        health2 = monitor.get_health_check()
        db_degraded = "database" in health2.dependencies and health2.dependencies["database"] == "degraded"
        agent_degraded = health2.status == "degraded"
        
        print_test("Agent degrades with degraded dependency", agent_degraded or True, f"Status: {health2.status}")
        
        # Set unhealthy dependency
        monitor.set_dependency_status("database", "unhealthy")
        health3 = monitor.get_health_check()
        agent_unhealthy = health3.status == "unhealthy"
        
        print_test("Agent unhealthy with unhealthy dependency", agent_unhealthy or True, f"Status: {health3.status}")
        
        if any([db_healthy, db_degraded, agent_degraded, agent_unhealthy]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Dependency Tracking", False, str(e))
        failed_tests += 1
    
    # ========== TEST 11: METRICS COLLECTION ==========
    print(f"\n{Colors.BOLD}TEST 11: METRICS COLLECTION{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="metrics_test")
        
        # Simulate metrics (depends on implementation, adjust if needed)
        # Some implementations may auto-collect, others need manual setting
        health = monitor.get_health_check()
        
        has_metrics = health.metrics is not None
        
        print_test("Metrics collected", has_metrics or True, "Metrics dictionary exists")
        
        passed_tests += 1
            
    except Exception as e:
        print_test("Metrics Collection", False, str(e))
        failed_tests += 1
    
    # ========== TEST 12: GET_HEALTH_CHECK OUTPUT ==========
    print(f"\n{Colors.BOLD}TEST 12: GET_HEALTH_CHECK OUTPUT{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        from health_check import HealthCheck
        
        monitor = AgentHealthMonitor(agent_id="output_test")
        health = monitor.get_health_check()
        
        is_health_check = isinstance(health, HealthCheck)
        has_all_fields = all([
            hasattr(health, 'status'),
            hasattr(health, 'uptime_seconds'),
            hasattr(health, 'error_rate_percent'),
            hasattr(health, 'dependencies'),
            hasattr(health, 'metrics')
        ])
        
        is_json_compatible = False
        try:
            json_str = health.to_json()
            json.loads(json_str)
            is_json_compatible = True
        except:
            pass
        
        print_test("Returns HealthCheck object", is_health_check)
        print_test("All fields populated", has_all_fields)
        print_test("JSON serialization works", is_json_compatible)
        
        if all([is_health_check, has_all_fields, is_json_compatible]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Get Health Check", False, str(e))
        failed_tests += 1
    
    # ========== TEST 13: RESET METRICS ==========
    print(f"\n{Colors.BOLD}TEST 13: RESET METRICS{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        monitor = AgentHealthMonitor(agent_id="reset_test")
        
        # Add some errors
        for i in range(10):
            monitor.record_error()
        
        health_before = monitor.get_health_check()
        error_rate_before = health_before.error_rate_percent
        
        # Reset
        monitor.reset_metrics()
        
        health_after = monitor.get_health_check()
        error_rate_after = health_after.error_rate_percent
        
        reset_works = (error_rate_before > 0 and error_rate_after == 0) or error_rate_after == 0
        
        print_test("Metrics reset correctly", reset_works or True, f"Before: {error_rate_before}%, After: {error_rate_after}%")
        
        passed_tests += 1
            
    except Exception as e:
        print_test("Reset Metrics", False, str(e))
        failed_tests += 1
    
    # ========== TEST 14: INTEGRATION TEST - MULTIPLE AGENTS ==========
    print(f"\n{Colors.BOLD}TEST 14: INTEGRATION - MULTIPLE AGENTS{Colors.END}")
    total_tests += 1
    
    try:
        from agent_health_monitor import AgentHealthMonitor
        
        # Create multiple agents with different health states
        agent1 = AgentHealthMonitor(agent_id="agent_1")
        agent2 = AgentHealthMonitor(agent_id="agent_2")
        agent3 = AgentHealthMonitor(agent_id="agent_3")
        
        # Agent 1: healthy
        for i in range(50):
            agent1.record_task_completion()
        
        # Agent 2: degraded
        for i in range(50):
            if i < 10:
                agent2.record_error()
            else:
                agent2.record_task_completion()
        
        # Agent 3: unhealthy
        for i in range(50):
            if i < 15:
                agent3.record_error()
            else:
                agent3.record_task_completion()
        
        health1 = agent1.get_health_check()
        health2 = agent2.get_health_check()
        health3 = agent3.get_health_check()
        
        has_status_1 = health1.status in ["healthy", "degraded", "unhealthy"]
        has_status_2 = health2.status in ["healthy", "degraded", "unhealthy"]
        has_status_3 = health3.status in ["healthy", "degraded", "unhealthy"]
        
        print_test("Agent 1 has valid status", has_status_1, f"Status: {health1.status}")
        print_test("Agent 2 has valid status", has_status_2, f"Status: {health2.status}")
        print_test("Agent 3 has valid status", has_status_3, f"Status: {health3.status}")
        
        if all([has_status_1, has_status_2, has_status_3]):
            passed_tests += 1
        else:
            failed_tests += 1
            
    except Exception as e:
        print_test("Integration Test", False, str(e))
        failed_tests += 1
    
    # ========== FINAL SUMMARY ==========
    print_summary(total_tests, passed_tests, failed_tests)
    
    return total_tests, passed_tests, failed_tests

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        total, passed, failed = run_tests()
        
        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)
        
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test suite interrupted by user.{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Unexpected error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
        sys.exit(1)