import json
import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ingestion_server import app, AGENTS, METRICS


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_register_and_telemetry(client):
    # register
    rv = client.post("/register", json={"agent_id": "a1", "info": {"v": "1"}})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["agent_id"] == "a1"

    # telemetry
    rv = client.post("/telemetry", json={"agent_id": "a1", "timestamp": 12345, "metrics": {"cpu": 5}})
    assert rv.status_code == 200
    assert "a1" in METRICS
