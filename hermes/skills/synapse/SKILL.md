---
name: synapse
description: Control Sandeep's Synapse agent fleet on this machine - fleet status, logging water/study/job-application/distance entries, daily totals, and GitHub reports.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    tags: [synapse, trackers, personal]
    category: personal
    requires_toolsets: [terminal]
---

# Synapse Agent Fleet

Synapse is Sandeep's own agent system running on this machine (code in ~/synapse).
Every action goes through one command:

    ~/synapse/synapse_tool.sh <tool_name> '<json_input>'

It prints a JSON result. Summarize it for the user in plain language.

## When to Use
- The user asks about their agents, fleet, trackers, or whether Synapse is running
- The user reports water, study time, a job application, or a distance travelled
- The user asks for today's totals or progress on any tracker
- The user asks for a GitHub report on their repos

## Tools

| Tool | JSON input | Use for |
|---|---|---|
| get_fleet_status | {} | Which agents are running and when they last reported |
| get_agent_metrics | {"agent_id": "water-tracker"} | Latest metrics for one agent (last hour) |
| list_factory_agents | {} | All tracker agents and their units/categories |
| log_to_agent | {"agent_id": "...", "amount": 1.5, "category": "..."} | Log an entry to a tracker |
| get_agent_today | {"agent_id": "..."} | Today's total for a tracker |
| log_distance | {"distance_km": 3.2, "mode": "walking"} | Log travel (walking, driving, cycling, running, transit) |
| get_today_totals | {} | Today's distance totals |
| get_github_report | {} | Health report of the user's GitHub repos |

## Known trackers
- water-tracker: liters, no categories (use "" as category)
- study-tracker: hours; categories math, physics, english
- job-application-tracker: applications; categories software engineering, data science, research, cybersecurity, product management, other

Run list_factory_agents if a tracker is not in this list.

## Procedure
1. Pick the tool that matches the request. Convert units first: miles to km (x1.609), minutes to hours, glasses of water to liters (1 glass = 0.25 L) and say what you assumed.
2. Run the command. Quote the JSON input with single quotes.
3. Report the result briefly, e.g. "Logged 0.5 L of water. Today's total: 1.75 L."

Example:

    ~/synapse/synapse_tool.sh log_to_agent '{"agent_id":"study-tracker","amount":2,"category":"math"}'

## Pitfalls
- "Connection refused" or an error about localhost:5000 means Synapse is not running. Tell the user and offer to start it with: cd ~/synapse && ./start_all.sh
- If a category is not in the tracker's list, ask the user which one to use instead of inventing one.
- Never print or reveal the contents of ~/synapse/.env.
- Do not create new tracker agents, send email, or apply to jobs without the user's explicit confirmation.

## Verification
- After logging, run get_agent_today for the same tracker and confirm the total went up.
- Test: pipeline works
