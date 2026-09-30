#!/usr/bin/env bash
# Start (or restart) the AI-Job-Agent backend on localhost:8000.
# Usage: start_job_agent.sh [restart]
cd "$HOME/job-agent/backend" || { echo "~/job-agent not found"; exit 1; }
if [ "$1" = "restart" ]; then
  pkill -f "uvicorn main:app" 2>/dev/null
  sleep 2
fi
if pgrep -f "uvicorn main:app" >/dev/null; then
  echo "Job agent already running on http://127.0.0.1:8000"
  exit 0
fi
nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 > "$HOME/job-agent/server.log" 2>&1 &
sleep 3
if curl -s -o /dev/null http://127.0.0.1:8000/; then
  echo "Job agent started on http://127.0.0.1:8000"
else
  echo "Job agent failed to start - see ~/job-agent/server.log"
fi
