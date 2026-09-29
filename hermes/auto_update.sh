#!/usr/bin/env bash
# Runs on the HP every 10 minutes (cron). Pulls new code from GitHub and
# restarts Synapse + Hermes only when something actually changed.
LOG="$HOME/.synapse_auto_update.log"
HERMES="$HOME/.local/bin/hermes"
cd "$HOME/synapse" || exit 1

git fetch -q origin 2>>"$LOG" || exit 0
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0

if git pull -q --ff-only 2>>"$LOG"; then
  mkdir -p logs
  chmod +x synapse_tool.sh start_all.sh stop_all.sh hermes/auto_update.sh 2>/dev/null
  # Install any new Python packages, then restart everything
  .venv/bin/pip install -q -r requirements.txt >>"$LOG" 2>&1
  ./stop_all.sh >/dev/null 2>&1
  ./start_all.sh >/dev/null 2>&1
  "$HERMES" gateway restart >/dev/null 2>&1
  echo "$(date '+%F %T') updated ${LOCAL:0:7} -> $(git rev-parse --short HEAD)" >>"$LOG"
else
  echo "$(date '+%F %T') pull FAILED - local changes on the HP? run: cd ~/synapse && git status" >>"$LOG"
fi
