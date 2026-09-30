# Synapse / Jarvis — project notes for Claude Code

## What this is
This repo (github.com/sande231/Repoter, "Synapse") is part of Sandeep's "Jarvis":
Telegram (@Sande_jarvis_bot) -> Hermes Agent (runs 24/7 on an old HP laptop) -> this code as tools.

- **Mac** (this machine): where code is written. Repo at `~/Desktop/synapse`.
- **HP** (Windows 11 + WSL Ubuntu 24.04, user `sande`): runs everything. Repo at `~/synapse`.
- **AI-Job-Agent** (github.com/sande231/AI-Job-Agent): separate repo. On Mac at `~/AI-Job-Agent`,
  on HP at `~/job-agent`. FastAPI backend on `127.0.0.1:8000`.

## How code reaches the HP
`git push` from the Mac is a deploy. The HP runs `hermes/auto_update.sh` every 10 minutes (cron):
it pulls `~/synapse` and `~/job-agent`, installs requirements, symlinks any new folder in
`hermes/skills/` into `~/.hermes/skills/`, restarts Synapse (`start_all.sh`) and `hermes gateway restart`,
and restarts the job agent. Log: `~/.synapse_auto_update.log` on the HP.
So: only push code that works. Test locally first when possible.

## Key files
- `smart_chat.py` — tool definitions + `execute_tool(name, input)`
- `synapse_tool.sh` — bash bridge Hermes uses: `~/synapse/synapse_tool.sh <tool_name> '<json>'`
- `hermes/skills/<name>/SKILL.md` — Hermes skills (Markdown with YAML frontmatter:
  name, description, version, platforms, metadata.hermes.tags/category/requires_toolsets).
  To add a Jarvis capability: add a new skill folder here and push.
- `hermes/auto_update.sh`, `hermes/start_job_agent.sh` — HP deploy scripts (must stay executable)
- `start_all.sh` / `stop_all.sh` — start/stop Synapse. Must NOT start the old Telegram bot
  (Hermes owns the Telegram token; two pollers would conflict).
- Ingestion server: `localhost:5000`, header `X-API-KEY`.
- Trackers: water-tracker (liters), study-tracker (hours; math/physics/english),
  job-application-tracker, distance tracker.

## Rules
- Never commit secrets: `.env`, `logs/`, tokens, API keys. A bot token leaked once via `logs/` — check `git status` before every commit.
- Never print `.env` contents.
- Skills must require explicit user confirmation before: sending email, creating agents,
  applying to jobs, or anything that costs money (OpenAI calls).
- Keep everything free where possible: Hermes uses the free Nous Portal model; Whisper and edge-tts run locally.
- The HP's WSL network is slow/flaky: keep dependencies light; pip there uses long timeouts.

## Style
Sandeep is a CS student and prefers short, concrete, numbered steps.
When a change needs something run on the HP, give the exact commands for the HP's Ubuntu terminal.
