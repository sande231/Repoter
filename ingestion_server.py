"""Lightweight ingestion server (MVP).

Provides endpoints for agent registration and telemetry ingestion. Stores data in-memory.
"""
import logging
import os
import time
from collections import defaultdict
from functools import wraps

from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory stores (MVP)
AGENTS = {}  # agent_id -> {info..., last_seen}
METRICS = defaultdict(list)  # agent_id -> list of metric envelopes

INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY")
API_KEY_HEADER = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def require_api_key(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not INGESTION_API_KEY:
            return view_func(*args, **kwargs)
        key = request.headers.get(API_KEY_HEADER) or request.headers.get("Authorization", "").replace("Bearer ", "")
        if key != INGESTION_API_KEY:
            logger.warning("Unauthorized request to %s from %s", request.path, request.remote_addr)
            return jsonify({"error": "unauthorized"}), 401
        return view_func(*args, **kwargs)
    return wrapper


@app.route("/register", methods=["POST"])
@require_api_key
def register():
    payload = request.json or {}
    agent_id = payload.get("agent_id")
    if not agent_id:
        logger.warning("Registration request missing agent_id")
        return jsonify({"error": "agent_id required"}), 400
    AGENTS[agent_id] = {"info": payload.get("info", {}), "tags": payload.get("tags", {}), "last_seen": int(time.time())}
    logger.info("Registered agent %s", agent_id)
    return jsonify({"status": "ok", "agent_id": agent_id})


@app.route("/telemetry", methods=["POST"])
@require_api_key
def telemetry():
    payload = request.json or {}
    agent_id = payload.get("agent_id")
    if not agent_id:
        logger.warning("Telemetry request missing agent_id")
        return jsonify({"error": "agent_id required"}), 400
    entry = {"timestamp": payload.get("timestamp", int(time.time())), "metrics": payload.get("metrics", {}), "host": payload.get("host"), "tags": payload.get("tags", {})}
    METRICS[agent_id].append(entry)
    AGENTS.setdefault(agent_id, {}).update({"last_seen": int(time.time())})
    logger.debug("Telemetry received from %s: %s", agent_id, entry)
    return jsonify({"status": "ok"})


@app.route("/agents", methods=["GET"])
@require_api_key
def list_agents():
    out = []
    for aid, info in AGENTS.items():
        out.append({"agent_id": aid, "info": info})
    return jsonify(out)


@app.route("/metrics/<agent_id>", methods=["GET"])
@require_api_key
def get_metrics(agent_id):
    since = request.args.get("since", type=int)
    data = METRICS.get(agent_id, [])
    if since:
        data = [d for d in data if d["timestamp"] >= since]
    return jsonify(data)


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "agents": len(AGENTS), "queued_metrics": sum(len(v) for v in METRICS.values())})


@app.route("/metrics", methods=["GET"])
def ingestion_metrics():
    return jsonify({
        "agent_count": len(AGENTS),
        "metric_entries": sum(len(v) for v in METRICS.values()),
        "uptime": int(time.time()),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
