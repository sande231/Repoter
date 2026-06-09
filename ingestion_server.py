"""Lightweight ingestion server (MVP).

Provides endpoints for agent registration and telemetry ingestion. Stores data in-memory.
"""
from flask import Flask, request, jsonify
import time
from collections import defaultdict

app = Flask(__name__)

# In-memory stores (MVP)
AGENTS = {}  # agent_id -> {info..., last_seen}
METRICS = defaultdict(list)  # agent_id -> list of metric envelopes


@app.route("/register", methods=["POST"])
def register():
    payload = request.json or {}
    agent_id = payload.get("agent_id")
    if not agent_id:
        return jsonify({"error": "agent_id required"}), 400
    AGENTS[agent_id] = {"info": payload.get("info", {}), "tags": payload.get("tags", {}), "last_seen": int(time.time())}
    return jsonify({"status": "ok", "agent_id": agent_id})


@app.route("/telemetry", methods=["POST"])
def telemetry():
    payload = request.json or {}
    agent_id = payload.get("agent_id")
    if not agent_id:
        return jsonify({"error": "agent_id required"}), 400
    entry = {"timestamp": payload.get("timestamp", int(time.time())), "metrics": payload.get("metrics", {}), "host": payload.get("host"), "tags": payload.get("tags", {})}
    METRICS[agent_id].append(entry)
    AGENTS.setdefault(agent_id, {})["last_seen"] = int(time.time())
    return jsonify({"status": "ok"})


@app.route("/agents", methods=["GET"])
def list_agents():
    out = []
    for aid, info in AGENTS.items():
        out.append({"agent_id": aid, "info": info})
    return jsonify(out)


@app.route("/metrics/<agent_id>", methods=["GET"])
def get_metrics(agent_id):
    since = request.args.get("since", type=int)
    data = METRICS.get(agent_id, [])
    if since:
        data = [d for d in data if d["timestamp"] >= since]
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
