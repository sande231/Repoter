import os
import sys
import time
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import main_agent as mg


def test_find_stale_agents():
    now = int(time.time())
    agents = [
        {"agent_id": "a1", "info": {"last_seen": now - 700}},
        {"agent_id": "a2", "info": {"last_seen": now - 100}},
    ]

    stale = mg.find_stale_agents(agents, threshold_seconds=600)
    assert len(stale) == 1
    assert stale[0]["agent_id"] == "a1"
    assert stale[0]["age_seconds"] >= 700


def test_render_report_includes_stale_agents():
    stale_agents = [{"agent_id": "a1", "last_seen": 1, "age_seconds": 700}]
    html = mg.render_report([], stale_agents=stale_agents)

    assert "Stale agents detected" in html
    assert "a1" in html


def test_parse_agent_problems():
    metrics = [
        {
            "timestamp": 1650000000,
            "metrics": {
                "status": "problem",
                "problem": {
                    "message": "Disk pressure",
                    "severity": "critical",
                },
            },
        }
    ]

    problems = mg.parse_agent_problems(metrics)
    assert len(problems) == 1
    assert problems[0]["message"] == "Disk pressure"
    assert problems[0]["severity"] == "critical"
    assert problems[0]["status"] == "problem"


def test_attempt_fix_agent_success(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, ok, status_code):
            self.ok = ok
            self.status_code = status_code

    def fake_post(url, timeout):
        calls.append(url)
        return FakeResponse(ok=True, status_code=200)

    monkeypatch.setattr(mg.session, "post", fake_post)
    fix_result = mg.attempt_fix_agent({"control_url": "http://agent.local/control"}, "agent1")

    assert fix_result["success"] is True
    assert fix_result["endpoint"] == "http://agent.local/control"
    assert calls[0] == "http://agent.local/control"


def test_attempt_fix_agent_missing_control_url():
    fix_result = mg.attempt_fix_agent({}, "agent1")
    assert fix_result["success"] is False
    assert fix_result["reason"] == "missing control_url"


def test_extract_agent_info_from_ingestion_record():
    agent = {
        "agent_id": "canvas-tutor-agent",
        "info": {
            "info": {
                "name": "Canvas Tutor",
                "control_url": "http://localhost:9000/control",
            },
            "tags": {"agent_type": "canvas_tutor"},
            "last_seen": 123,
        },
    }

    info = mg.extract_agent_info(agent)

    assert info["name"] == "Canvas Tutor"
    assert info["control_url"] == "http://localhost:9000/control"
