#!/usr/bin/env bash
# Synapse bridge for Hermes.
# Usage: synapse_tool.sh <tool_name> '<json_input>'
# Example: synapse_tool.sh log_to_agent '{"agent_id":"water-tracker","amount":0.5,"category":""}'
cd "$HOME/synapse" || { echo '{"error":"~/synapse not found"}'; exit 1; }

# Load Synapse settings from .env (keys stay on this machine)
set -a
while IFS= read -r line || [ -n "$line" ]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  export "$line"
done < .env
set +a

.venv/bin/python -c '
import sys, json
from smart_chat import execute_tool
name = sys.argv[1] if len(sys.argv) > 1 else ""
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
print(execute_tool(name, args))
' "$@"
