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
import uuid
import requests
import anthropic
from distance_tracker import DistanceTracker

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:5000")
INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY", "")
INGESTION_API_KEY_HEADER = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
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
    {
        "name": "create_tracker_agent",
        "description": "CREATE A BRAND NEW tracker agent when the user asks to make/build an agent that tracks something (study hours, water intake, pushups, expenses, etc.). Generates a complete self-healing agent file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Short lowercase id with hyphens, e.g. 'study-tracker'"},
                "name": {"type": "string", "description": "Human-friendly name, e.g. 'Study Tracker'"},
                "description": {"type": "string", "description": "One line: what it tracks"},
                "unit": {"type": "string", "description": "Unit of measurement, e.g. 'hours', 'liters', 'reps', 'dollars'"},
                "categories": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Allowed categories, e.g. ['math','physics']. Empty list = any category allowed.",
                },
            },
            "required": ["agent_id", "name", "description", "unit", "categories"],
        },
    },
    {
        "name": "start_factory_agent",
        "description": "Start a factory-created agent so it registers with Synapse and reports telemetry. Use after creating an agent, or when the user asks to start one.",
        "input_schema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        },
    },
    {
        "name": "list_factory_agents",
        "description": "List all agents created by the factory.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "log_to_agent",
        "description": "Log an entry into any factory-created agent (e.g. 'log 2 hours of math' into study-tracker). NOT for distances - use log_distance for travel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "amount": {"type": "number"},
                "category": {"type": "string"},
            },
            "required": ["agent_id", "amount", "category"],
        },
    },
    {
        "name": "get_agent_today",
        "description": "Get today's totals for a factory-created agent.",
        "input_schema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        },
    },
    {
        "name": "get_github_report",
        "description": "Get a live summary of the user's GitHub repositories and GitHub Actions workflow health - successes, failures, and repos needing attention. Use for requests like 'give me a report on my repos/reporter agent', 'how's my GitHub CI doing', 'any failing workflows'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stage_email",
        "description": "Stage a fully-drafted email for the user to review. This does NOT send the email - it only prepares it so the user can approve or discard it themselves via a button in the chat. Call this once you have composed the complete final subject and body the user wants to send, with a real recipient address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Full email body text."},
            },
            "required": ["recipient", "subject", "body"],
        },
    },
]

# Per-chat pending email drafts awaiting explicit user approval: {chat_id: {token, recipient, subject, body}}
# NOTE: sending only ever happens when the human taps the Send button wired to this dict in
# telegram_bot.py - no tool here is capable of actually dispatching an email.
PENDING_EMAILS = {}

# ---------------------------------------------------------------
# 2. TOOL EXECUTION - your existing code does the real work
# ---------------------------------------------------------------

tracker = DistanceTracker()


def execute_tool(name: str, tool_input: dict, chat_id=None) -> str:
    """Run the requested tool and return a JSON result string."""
    chat_id = chat_id if chat_id is not None else "cli"
    try:
        if name == "log_distance":
            totals = tracker.log_distance(tool_input["distance_km"], tool_input["mode"])
            return json.dumps({"logged": True, "today": totals})

        if name == "get_today_totals":
            return json.dumps(tracker.get_today_totals())

        if name == "get_fleet_status":
            resp = requests.get(
                f"{INGESTION_URL}/agents",
                headers={INGESTION_API_KEY_HEADER: INGESTION_API_KEY},
                timeout=3,
            )
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
                headers={INGESTION_API_KEY_HEADER: INGESTION_API_KEY},
                timeout=3,
            )
            data = resp.json()
            latest = data[-1]["metrics"] if data else {}
            return json.dumps({"entries_last_hour": len(data), "latest_metrics": latest})

        if name == "create_tracker_agent":
            import agent_factory
            return json.dumps(agent_factory.create_agent(
                tool_input["agent_id"], tool_input["name"],
                tool_input["description"], tool_input["unit"],
                tool_input.get("categories", []),
            ))

        if name == "start_factory_agent":
            import agent_factory
            return json.dumps(agent_factory.start_agent(tool_input["agent_id"]))

        if name == "list_factory_agents":
            import agent_factory
            return json.dumps(agent_factory.list_agents())

        if name == "log_to_agent":
            import agent_factory
            return json.dumps(agent_factory.log_entry(
                tool_input["agent_id"], tool_input["amount"], tool_input["category"],
            ))

        if name == "get_agent_today":
            import agent_factory
            return json.dumps(agent_factory.get_today(tool_input["agent_id"]))

        if name == "get_github_report":
            import central_reporter
            username = os.environ.get("TARGET_GITHUB_USERNAME") or central_reporter.GITHUB_USER
            if not username:
                return json.dumps({"error": "TARGET_GITHUB_USERNAME is not configured; cannot fetch GitHub report."})
            repos_report = central_reporter.gather_report(username)
            summary = central_reporter.summarize_report(repos_report)
            needs_attention = [
                {"name": r["name"], "reason": r["health"]["reason"], "url": r.get("html_url")}
                for r in repos_report if r["health"]["needs_attention"]
            ]
            return json.dumps({"summary": summary, "needs_attention": needs_attention})

        if name == "stage_email":
            token = uuid.uuid4().hex[:8]
            PENDING_EMAILS[chat_id] = {
                "token": token,
                "recipient": tool_input["recipient"],
                "subject": tool_input["subject"],
                "body": tool_input["body"],
            }
            return json.dumps({
                "staged": True,
                "note": "Draft staged for the user's review. It will NOT be sent unless the user taps Send on the button shown in the chat.",
            })

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
- Report on GitHub repository and CI/workflow health (successes, failures, repos needing attention)
- Draft emails on request (e.g. "write an email to my professor asking for an extension")
- CREATE NEW TRACKER AGENTS on request (study hours, water intake, pushups, expenses, anything measurable)
- Log entries into any created agent and report its totals

When the user asks you to write an email:
1. Compose the full subject and body yourself, in your normal reply, so they can read it first.
2. Only call stage_email once you have a complete draft with a real recipient address. You have
   no way to actually send email yourself - stage_email only prepares it for the user to approve
   or discard via a button shown in their chat. Never imply the email has been sent.

When the user asks you to make/build an agent:
1. Pick a sensible agent_id, unit, and categories (confirm with the user if ambiguous)
2. Call create_tracker_agent
3. Then call start_factory_agent so it joins the Synapse fleet
4. Tell them how to log entries (they can just tell you naturally)

Keep replies short and friendly. Use tools whenever relevant. Convert miles to km."""


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