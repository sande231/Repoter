"""
Synapse Smart Chat - Natural language interface to your agent fleet.
Powered by Claude API with tool use.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python smart_chat.py
"""

import os
import json
import time
import requests
import anthropic
from distance_tracker import DistanceTracker

INGESTION_URL = "http://localhost:5000"
MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------
# 1. THE TOOLS - what Claude is allowed to do
# ---------------------------------------------------------------

TOOLS = [
    {
        "name": "log_distance",
        "description": "Log a distance the user traveled. Use when the user mentions walking, driving, cycling, running, or taking transit somewhere.",
        "input_schema": {
            "type": "object",
            "properties": {
                "distance_km": {
                    "type": "number",
                    "description": "Distance in kilometers. Convert miles to km if needed (1 mile = 1.609 km).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["walking", "driving", "cycling", "running", "transit"],
                },
            },
            "required": ["distance_km", "mode"],
        },
    },
    {
        "name": "get_today_totals",
        "description": "Get today's distance totals broken down by travel mode.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_fleet_status",
        "description": "Get status of all registered Synapse agents (the monitoring fleet).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_agent_metrics",
        "description": "Get recent telemetry metrics for a specific agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "e.g. 'distance-tracker' or 'canvas-tutor-agent'",
                },
            },
            "required": ["agent_id"],
        },
    },
]

# ---------------------------------------------------------------
# 2. TOOL EXECUTION - your existing code does the real work
# ---------------------------------------------------------------

tracker = DistanceTracker()


def execute_tool(name: str, tool_input: dict) -> str:
    """Run the requested tool and return a JSON result string."""
    try:
        if name == "log_distance":
            totals = tracker.log_distance(tool_input["distance_km"], tool_input["mode"])
            return json.dumps({"logged": True, "today": totals})

        if name == "get_today_totals":
            return json.dumps(tracker.get_today_totals())

        if name == "get_fleet_status":
            resp = requests.get(f"{INGESTION_URL}/agents", timeout=3)
            agents = resp.json()
            now = int(time.time())
            out = []
            for a in agents:
                last_seen = int(a.get("info", {}).get("last_seen", 0))
                out.append({
                    "agent_id": a["agent_id"],
                    "name": a.get("info", {}).get("info", {}).get("name", a["agent_id"]),
                    "seconds_since_last_seen": now - last_seen,
                })
            return json.dumps({"agent_count": len(out), "agents": out})

        if name == "get_agent_metrics":
            since = int(time.time()) - 3600
            resp = requests.get(
                f"{INGESTION_URL}/metrics/{tool_input['agent_id']}",
                params={"since": since},
                timeout=3,
            )
            data = resp.json()
            latest = data[-1]["metrics"] if data else {}
            return json.dumps({"entries_last_hour": len(data), "latest_metrics": latest})

        return json.dumps({"error": f"Unknown tool: {name}"})

    except requests.exceptions.ConnectionError:
        return json.dumps({"error": "Ingestion server unreachable (port 5000). Is it running?"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------
# 3. THE CONVERSATION LOOP - Claude decides, tools execute
# ---------------------------------------------------------------

SYSTEM_PROMPT = """You are the Synapse assistant - a friendly helper managing the user's personal agent fleet.

You can:
- Log distances the user traveled (walking/driving/cycling/running/transit)
- Report today's travel totals
- Check the health/status of their monitoring agents

Keep replies short and friendly. Use the tools whenever relevant. If the user mentions traveling somewhere with a distance, log it. Convert miles to km."""


def chat_turn(client, messages):
    """One turn: send to Claude, execute any tool calls, return final text."""
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Collect any tool calls Claude wants to make
        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if not tool_calls:
            # No tools needed - return Claude's text reply
            text = "".join(b.text for b in response.content if b.type == "text")
            messages.append({"role": "assistant", "content": response.content})
            return text

        # Execute tools and send results back
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in tool_calls:
            print(f"   [tool: {call.name}] {json.dumps(call.input)}")
            result = execute_tool(call.name, call.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result,
            })
        messages.append({"role": "user", "content": results})
        # Loop again - Claude sees the results and responds (or calls more tools)


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set your API key first:  export ANTHROPIC_API_KEY='sk-ant-...'")
        return

    client = anthropic.Anthropic(api_key=api_key)
    messages = []

    print("=" * 55)
    print("  SYNAPSE SMART CHAT - powered by Claude")
    print("  Talk naturally! ('quit' to exit)")
    print("=" * 55)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye"}:
            print("Bye!")
            break

        messages.append({"role": "user", "content": user_input})
        try:
            reply = chat_turn(client, messages)
            print(f"\nBot: {reply}")
        except anthropic.APIError as e:
            print(f"\nAPI error: {e}")


if __name__ == "__main__":
    main()