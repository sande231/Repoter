import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agent_sdk import AgentSDK


def test_envelope_structure():
    sdk = AgentSDK(agent_id="test-agent", ingestion_url="http://example.local")
    env = sdk._envelope({"cpu": 1.2})
    assert env["agent_id"] == "test-agent"
    assert "timestamp" in env
    assert env["metrics"]["cpu"] == 1.2
