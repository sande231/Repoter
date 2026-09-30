#!/usr/bin/env bash
# Runs on the HP every 10 minutes (cron). Pulls new code from GitHub for
# Synapse (~/synapse) and the job agent (~/job-agent), links any new Hermes
# skills, and restarts only what changed.
LOG="$HOME/.synapse_auto_update.log"
HERMES="$HOME/.local/bin/hermes"
stamp() { date '+%F %T'; }

# pull_repo <dir> -> returns 0 if new code was pulled
pull_repo() {
  cd "$1" 2>/dev/null || return 1
  git fetch -q origin 2>>"$LOG" || return 1
  [ "$(git rev-parse HEAD)" = "$(git rev-parse '@{u}')" ] && return 1
  local old; old=$(git rev-parse --short HEAD)
  if git pull -q --ff-only 2>>"$LOG"; then
    echo "$(stamp) $1 updated $old -> $(git rev-parse --short HEAD)" >>"$LOG"
    return 0
  fi
  echo "$(stamp) $1 pull FAILED - local changes? run: cd $1 && git status" >>"$LOG"
  return 1
}

# --- Synapse ---
if pull_repo "$HOME/synapse"; then
  cd "$HOME/synapse"
  mkdir -p logs
  chmod +x synapse_tool.sh start_all.sh stop_all.sh hermes/*.sh 2>/dev/null
  .venv/bin/pip install -q -r requirements.txt >>"$LOG" 2>&1
  # Link every skill folder in the repo into Hermes (new skills go live)
  for d in "$HOME"/synapse/hermes/skills/*/; do
    n=$(basename "$d")
    [ -e "$HOME/.hermes/skills/$n" ] || ln -s "${d%/}" "$HOME/.hermes/skills/$n"
  done
  ./stop_all.sh >/dev/null 2>&1
  ./start_all.sh >/dev/null 2>&1
  "$HERMES" gateway restart >/dev/null 2>&1
fi

# --- Job agent ---
if [ -d "$HOME/job-agent" ] && pull_repo "$HOME/job-agent"; then
  cd "$HOME/job-agent/backend"
  .venv/bin/pip install -q -r requirements.txt >>"$LOG" 2>&1
  "$HOME/synapse/hermes/start_job_agent.sh" restart
fi
