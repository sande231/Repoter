import io
import json
import logging
import os
import pytest
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from structured_logger import StructuredLogger, log_execution


def _logger_with_stream():
    stream = io.StringIO()
    logger = logging.getLogger(f"test-structured-{id(stream)}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, stream


def _read_json_log(stream):
    return json.loads(stream.getvalue().strip())


def _read_json_logs(stream):
    return [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]


def test_log_event_outputs_json_with_required_fields():
    logger, stream = _logger_with_stream()
    structured = StructuredLogger("agent-1", logger=logger)

    record = structured.log_event("startup", duration_ms=12.5, status="ok")
    logged = _read_json_log(stream)

    assert logged == record
    assert logged["agent_id"] == "agent-1"
    assert logged["event_type"] == "event"
    assert logged["event_name"] == "startup"
    assert logged["duration_ms"] == 12.5
    assert logged["status"] == "ok"
    assert datetime.fromisoformat(logged["timestamp"].replace("Z", "+00:00"))


def test_log_metric_outputs_metric_payload():
    logger, stream = _logger_with_stream()
    structured = StructuredLogger("agent-1", logger=logger)

    structured.log_metric("queue_depth", 3)
    logged = _read_json_log(stream)

    assert logged["agent_id"] == "agent-1"
    assert logged["event_type"] == "metric"
    assert logged["metric_name"] == "queue_depth"
    assert logged["value"] == 3
    assert logged["duration_ms"] == 0


def test_log_error_outputs_error_payload_without_mutating_context():
    logger, stream = _logger_with_stream()
    structured = StructuredLogger("agent-1", logger=logger)
    context = {"operation": "send_metrics", "duration_ms": 9}

    try:
        raise ValueError("bad telemetry")
    except ValueError as exc:
        structured.log_error(exc, context)

    logged = _read_json_log(stream)

    assert context == {"operation": "send_metrics", "duration_ms": 9}
    assert logged["agent_id"] == "agent-1"
    assert logged["event_type"] == "error"
    assert logged["duration_ms"] == 9
    assert logged["error_type"] == "ValueError"
    assert logged["error_message"] == "bad telemetry"
    assert "ValueError: bad telemetry" in logged["stack_trace"]
    assert logged["context"] == {"operation": "send_metrics"}


def test_log_execution_logs_entry_exit_parameters_and_return_value():
    logger, stream = _logger_with_stream()

    @log_execution(logger=logger, agent_id="agent-1")
    def build_payload(course_id, access_token="secret-token"):
        return {"course_id": course_id, "status": "ready"}

    result = build_payload(42)
    logs = _read_json_logs(stream)

    assert result == {"course_id": 42, "status": "ready"}
    assert len(logs) == 2
    assert logs[0]["event_type"] == "event"
    assert logs[0]["event_name"] == "function_entry"
    assert logs[0]["duration_ms"] == 0
    assert logs[0]["parameters"] == {
        "course_id": 42,
        "access_token": "[REDACTED]",
    }

    assert logs[1]["event_type"] == "event"
    assert logs[1]["event_name"] == "function_exit"
    assert logs[1]["duration_ms"] >= 0
    assert logs[1]["return_value"] == {"course_id": 42, "status": "ready"}


def test_log_execution_logs_exception_with_stack_trace():
    logger, stream = _logger_with_stream()

    @log_execution(logger=logger, agent_id="agent-1")
    def fail_job(password):
        raise RuntimeError("job failed")

    with pytest.raises(RuntimeError, match="job failed"):
        fail_job(password="open-sesame")

    logs = _read_json_logs(stream)

    assert len(logs) == 2
    assert logs[0]["event_name"] == "function_entry"
    assert logs[0]["parameters"]["password"] == "[REDACTED]"

    assert logs[1]["event_type"] == "error"
    assert logs[1]["error_type"] == "RuntimeError"
    assert logs[1]["error_message"] == "job failed"
    assert "RuntimeError: job failed" in logs[1]["stack_trace"]
    assert logs[1]["context"]["event_name"] == "function_error"
    assert logs[1]["context"]["parameters"]["password"] == "[REDACTED]"
    assert logs[1]["duration_ms"] >= 0
