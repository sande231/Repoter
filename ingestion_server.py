"""Lightweight ingestion server (MVP).

Provides endpoints for agent registration and telemetry ingestion. Stores data in-memory.
"""
import hmac
import html
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from collections import defaultdict
from functools import wraps

import anthropic
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, get_flashed_messages, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

import main_agent
import central_reporter
import agent_factory

app = Flask(__name__)
# Trust one hop of X-Forwarded-* headers - set by the Caddy/Nginx reverse proxy in front of
# this app in production, so request.is_secure and cookie handling behave correctly behind TLS.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("INGESTION_API_KEY") or os.urandom(24).hex()

# Dashboard login (separate from INGESTION_API_KEY, which is for machine-to-machine agent
# calls). Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH in .env before exposing this
# server publicly - with neither set, login-protected routes fail closed (503), they never
# silently open up.
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH", "")

# When running behind HTTPS (production), set FORCE_HTTPS_COOKIES=1 so the session cookie
# is only ever sent over TLS. Left off by default so local http://localhost testing still works.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FORCE_HTTPS_COOKIES") == "1"

limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["200 per hour"])

# In-memory stores (MVP)
AGENTS = {}  # agent_id -> {info..., last_seen}
METRICS = defaultdict(list)  # agent_id -> list of metric envelopes

INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY")
API_KEY_HEADER = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Cached GitHub report so /dashboard doesn't hit the GitHub API (and its rate limit) on every request.
GITHUB_REFRESH_SECONDS = int(os.environ.get("DASHBOARD_GITHUB_REFRESH_SECONDS", "600"))
_GITHUB_CACHE = {"summary": None, "repos": None, "generated_at": None, "error": None, "refresh_seconds": GITHUB_REFRESH_SECONDS}

# Real accumulated snapshots (one per refresh) so the trend chart reflects actual history,
# not fabricated data. Starts sparse right after a restart and fills in over time.
_GITHUB_HISTORY = []
_GITHUB_HISTORY_MAX = 500

# --- Phase 1: prompt-to-agent drafting (review-gated, nothing here executes automatically) ---
# Single global slot - this is a personal single-user dashboard with no session/login concept.
PENDING_AGENT_DRAFT = None  # {token, prompt, agent_id, filename, code, created_at}
APPROVED_AGENT_FILES = {}   # agent_id -> filename, populated once a draft is approved and saved

# --- Phase 3: explicit, per-agent-id trust. Checking "trust future revisions" at approval
# time collapses Approve+Start into one click for THAT agent_id from then on. It never
# skips drafting or the code review itself, and it never applies to an agent_id you haven't
# personally approved-with-trust before. Intentionally in-memory only (resets on restart) -
# a stale trust decision silently surviving indefinitely is worse than having to re-opt-in.
TRUSTED_AGENT_IDS = set()

_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _anthropic_client


AGENT_DRAFT_SYSTEM_PROMPT = """You write a single new Synapse fleet agent file from the user's plain-English \
description. This code will be reviewed by a human before it ever runs - it is never executed automatically.

Follow this exact structure, modeled on the existing canvas_tutor_adapter.py in this codebase:
- `from agent_sdk import AgentSDK`
- A class whose __init__ sets self.agent_id (a short kebab-case id) and builds
  self.sdk = AgentSDK(agent_id=self.agent_id, ingestion_url="http://localhost:5000",
  tags={"agent_type": "custom", "service": "<name>"})
- A register() method calling self.sdk.register({"name":..., "type":..., "description":..., "version": "1.0.0"})
- A method that does the agent's real work and reports it truthfully via self.sdk.send_metrics({...}) -
  never fabricate a fake "success" metric; if the work fails, call self.sdk.report_problem(...) instead.

The AgentSDK methods you may call have EXACTLY these signatures - match them precisely, do not guess:
- self.sdk.register(info: dict) - info is a dict of descriptive fields (name/type/description/version).
- self.sdk.send_metrics(metrics: dict) - metrics is a dict of real telemetry values.
- self.sdk.report_problem(message: str, severity: str = "critical", details: dict = None) - message is
  the human-readable description and MUST be the first positional argument (not an event slug); severity
  is one of "critical"/"warning"/"info"; details is optional structured context. Call it like:
  self.sdk.report_problem("Could not reach X: <reason>", severity="warning", details={...}).
- `if __name__ == "__main__":` that calls register() once, then loops forever: do the work, sleep, repeat.

Hard rules:
- No destructive operations: never delete/overwrite files other than this agent's own data file, never
  shell out to arbitrary commands, never touch other agents' files or the Synapse source code.
- No hardcoded secrets or API keys. If external credentials are needed, read them via
  os.environ.get("SOME_VAR") and if missing, call self.sdk.report_problem(...) with a clear message
  naming the required env var - never guess, invent, or silently skip the check.
- Only use well-known, genuinely public APIs/libraries for any external data source. If you are not
  confident a specific free API exists for the request, write the fetch as a clearly-labeled
  placeholder (a method that raises NotImplementedError with a comment explaining what real API call
  belongs there) rather than inventing a plausible-looking but fake endpoint.

Output format - exactly this, nothing else, no markdown fences, no explanation:
# AGENT_ID: <kebab-case-id>
# FILENAME: <snake_case>.py
<the complete Python file>"""


def _parse_agent_draft(raw_text):
    """Pull the AGENT_ID/FILENAME header lines and the code body out of the model's response."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:python)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    lines = text.splitlines()

    agent_id = None
    filename = None
    code_start = 0
    for i, line in enumerate(lines[:6]):
        id_match = re.match(r"#\s*AGENT_ID:\s*([a-z0-9\-]+)", line.strip())
        file_match = re.match(r"#\s*FILENAME:\s*([a-z0-9_]+\.py)", line.strip())
        if id_match:
            agent_id = id_match.group(1)
            code_start = max(code_start, i + 1)
        if file_match:
            filename = file_match.group(1)
            code_start = max(code_start, i + 1)

    code = "\n".join(lines[code_start:]).strip()
    return agent_id, filename, code


def _call_draft_model(messages):
    """Send the running draft conversation to Claude and return (ok, text_or_error)."""
    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=AGENT_DRAFT_SYSTEM_PROMPT,
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason == "max_tokens":
            logger.warning("Agent draft response was truncated by max_tokens")
            return False, "The model's response was cut off before finishing (too long). Try a simpler/more specific prompt."
        return True, text
    except Exception as exc:
        logger.warning("Agent draft model call failed: %s", exc)
        return False, str(exc)


def _code_compiles(code, filename):
    try:
        compile(code, filename, "exec")
        return True, None
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"


# --- Phase 2: static risk scan + line-numbered preview, so a draft is reviewed with the
# risky parts already flagged rather than read cold. Purely pattern-matching - a human
# still has to actually look, this just points at where to look first.
RISK_PATTERNS = [
    (re.compile(r"\bsubprocess\b"), "Runs a subprocess - confirm the command is safe and not built from untrusted input."),
    (re.compile(r"\bos\.system\s*\("), "Shells out via os.system - confirm the command is safe."),
    (re.compile(r"\beval\s*\("), "Uses eval() - can execute arbitrary code from a string."),
    (re.compile(r"\bexec\s*\("), "Uses exec() - can execute arbitrary code from a string."),
    (re.compile(r"\b__import__\s*\("), "Dynamic import - confirm what module this loads."),
    (re.compile(r"\bshutil\.rmtree\s*\("), "Recursively deletes a directory tree - confirm the path is scoped to this agent only."),
    (re.compile(r"\bos\.(remove|unlink)\s*\("), "Deletes a file - confirm the path is scoped to this agent only."),
    (re.compile(r"""open\([^)]*["'][wa]b?["']"""), "Opens a file for writing - confirm the path is this agent's own data file."),
    (re.compile(r"\brequests\.(get|post|put|delete|patch)\s*\("), "Makes an outbound network request - check the destination."),
    (re.compile(r"\burlopen\s*\("), "Makes an outbound network request - check the destination."),
    (re.compile(r"\bsocket\.(create_connection|connect)\s*\("), "Opens a raw network connection - check the destination."),
]
URL_PATTERN = re.compile(r'https?://[^\s\'"]+')


def _scan_code_risks(code):
    flags = []
    for i, line in enumerate(code.splitlines(), start=1):
        for pattern, warning in RISK_PATTERNS:
            if pattern.search(line):
                flags.append({"line": i, "snippet": line.strip(), "warning": warning})
        for url in URL_PATTERN.findall(line):
            flags.append({"line": i, "snippet": line.strip(), "warning": f"References external URL: {url}"})
    return flags


def _render_code_preview_html(code, risk_flags):
    """Line-numbered, HTML-escaped code block with flagged lines highlighted."""
    flagged_lines = {f["line"] for f in risk_flags}
    out = []
    for i, line in enumerate(code.splitlines(), start=1):
        escaped = html.escape(line) if line else ""
        cls = " risk-line" if i in flagged_lines else ""
        out.append(f'<span class="code-line{cls}"><span class="line-no">{i:>4}</span>{escaped}</span>')
    return "\n".join(out)


def refresh_github_cache():
    username = os.environ.get("TARGET_GITHUB_USERNAME")
    if not username:
        _GITHUB_CACHE["error"] = "TARGET_GITHUB_USERNAME is not set"
        return
    try:
        repos_report = central_reporter.gather_report(username)
        summary = central_reporter.summarize_report(repos_report)
        _GITHUB_CACHE["repos"] = repos_report
        _GITHUB_CACHE["summary"] = summary
        _GITHUB_CACHE["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _GITHUB_CACHE["error"] = None

        _GITHUB_HISTORY.append({
            "label": time.strftime("%m/%d %H:%M"),
            "successful": summary["successful"],
            "failed": summary["failed"],
            "in_progress": summary["in_progress"],
        })
        if len(_GITHUB_HISTORY) > _GITHUB_HISTORY_MAX:
            _GITHUB_HISTORY.pop(0)
    except Exception as exc:
        logger.warning("GitHub dashboard refresh failed: %s", exc)
        _GITHUB_CACHE["error"] = str(exc)


def require_api_key(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not INGESTION_API_KEY:
            return view_func(*args, **kwargs)
        key = (
            request.headers.get(API_KEY_HEADER)
            or request.headers.get("Authorization", "").replace("Bearer ", "")
            or request.form.get("api_key")
            or request.args.get("api_key")
        )
        if key != INGESTION_API_KEY:
            logger.warning("Unauthorized request to %s from %s", request.path, request.remote_addr)
            return jsonify({"error": "unauthorized"}), 401
        return view_func(*args, **kwargs)
    return wrapper


def login_required(view_func):
    """Gate the human-facing dashboard behind a real login session - separate from
    require_api_key, which is for machine agents calling /register, /telemetry, etc."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not DASHBOARD_PASSWORD_HASH:
            # Fail closed: no configured password means no access, not "no auth at all".
            return (
                "Dashboard login is not configured. Set DASHBOARD_USERNAME and "
                "DASHBOARD_PASSWORD_HASH in .env before this server is reachable "
                "from anywhere but your own machine.",
                503,
            )
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        valid_user = bool(DASHBOARD_USERNAME) and hmac.compare_digest(username, DASHBOARD_USERNAME)
        valid_pass = bool(DASHBOARD_PASSWORD_HASH) and check_password_hash(DASHBOARD_PASSWORD_HASH, password)
        if valid_user and valid_pass:
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        logger.warning("Failed dashboard login attempt from %s", request.remote_addr)
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


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


@app.route("/agents/<agent_id>/log", methods=["POST"])
@login_required
def log_task(agent_id):
    """Log an entry into a factory-created tracker agent - the same function
    the Telegram bot's log_to_agent tool calls, just triggered from the dashboard."""
    try:
        amount = float(request.form.get("amount", ""))
    except (TypeError, ValueError):
        flash(f"Couldn't log to {agent_id}: amount must be a number.")
        return redirect(url_for("dashboard"))

    category = (request.form.get("category") or "").strip()
    result = agent_factory.log_entry(agent_id, amount, category)
    if result.get("error"):
        flash(f"Couldn't log to {agent_id}: {result['error']}")
    else:
        flash(f"Logged {amount} to {agent_id} ({category}).")
    return redirect(url_for("dashboard"))


@app.route("/agents/draft", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def draft_agent():
    global PENDING_AGENT_DRAFT
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        flash("Enter a description of the agent you want to create.")
        return redirect(url_for("dashboard"))

    messages = [{"role": "user", "content": prompt}]
    ok, result = _call_draft_model(messages)
    if not ok:
        flash(f"Draft failed: {result}")
        return redirect(url_for("dashboard"))
    messages.append({"role": "assistant", "content": result})

    agent_id, filename, code = _parse_agent_draft(result)
    if not agent_id or not filename or not code:
        flash("Couldn't parse a valid draft from the model's response - try rephrasing your prompt.")
        return redirect(url_for("dashboard"))

    compiles, compile_error = _code_compiles(code, filename)
    if not compiles:
        flash(f"Draft for '{agent_id}' didn't come back as valid Python ({compile_error}) - try again, "
              f"possibly with a simpler prompt so the response doesn't get cut off.")
        return redirect(url_for("dashboard"))

    PENDING_AGENT_DRAFT = {
        "token": secrets.token_hex(6),
        "prompt": prompt,
        "refinements": [],
        "agent_id": agent_id,
        "filename": filename,
        "code": code,
        "messages": messages,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    flash(f"Draft ready for '{agent_id}' - review the code below before approving. Nothing has run yet.")
    return redirect(url_for("dashboard"))


@app.route("/agents/draft/refine", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def refine_draft():
    global PENDING_AGENT_DRAFT
    token = request.form.get("token")
    instruction = (request.form.get("instruction") or "").strip()

    if not PENDING_AGENT_DRAFT or PENDING_AGENT_DRAFT["token"] != token:
        flash("That draft is no longer active (expired or already handled).")
        return redirect(url_for("dashboard"))
    if not instruction:
        flash("Enter what you'd like changed before refining.")
        return redirect(url_for("dashboard"))

    draft = PENDING_AGENT_DRAFT
    messages = draft["messages"] + [{"role": "user", "content": instruction}]
    ok, result = _call_draft_model(messages)
    if not ok:
        flash(f"Refine failed: {result}")
        return redirect(url_for("dashboard"))
    messages.append({"role": "assistant", "content": result})

    agent_id, filename, code = _parse_agent_draft(result)
    if not agent_id or not filename or not code:
        flash("Couldn't parse a valid draft from the refined response - try a different instruction.")
        return redirect(url_for("dashboard"))

    compiles, compile_error = _code_compiles(code, filename)
    if not compiles:
        flash(f"Refined draft didn't come back as valid Python ({compile_error}) - the previous draft "
              f"below is unchanged, try refining again with a more specific instruction.")
        return redirect(url_for("dashboard"))

    PENDING_AGENT_DRAFT = {
        "token": secrets.token_hex(6),
        "prompt": draft["prompt"],
        "refinements": draft["refinements"] + [instruction],
        "agent_id": agent_id,
        "filename": filename,
        "code": code,
        "messages": messages,
        "created_at": draft["created_at"],
    }
    flash(f"Draft refined for '{agent_id}' - review the updated code below.")
    return redirect(url_for("dashboard"))


def _launch_agent_file(agent_id, filename):
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{agent_id}.log")
    log_file = open(log_path, "a")
    proc = subprocess.Popen([sys.executable, filename], stdout=log_file, stderr=log_file)
    logger.info("Launched custom agent %s from %s (PID %s), output -> %s", agent_id, filename, proc.pid, log_path)
    return proc.pid


@app.route("/agents/draft/approve", methods=["POST"])
@login_required
def approve_draft():
    global PENDING_AGENT_DRAFT
    token = request.form.get("token")
    if not PENDING_AGENT_DRAFT or PENDING_AGENT_DRAFT["token"] != token:
        flash("That draft is no longer active (expired or already handled).")
        return redirect(url_for("dashboard"))

    draft = PENDING_AGENT_DRAFT
    agent_id = draft["agent_id"]
    filename = draft["filename"]
    if os.path.exists(filename):
        flash(f"A file named {filename} already exists - discard this draft and try a different prompt.")
        return redirect(url_for("dashboard"))

    with open(filename, "w") as f:
        f.write(draft["code"])
    APPROVED_AGENT_FILES[agent_id] = filename

    # Phase 3: an explicit opt-in at approval time, OR an agent_id trusted from a
    # previous approval, collapses Approve+Start into this one click. Everything before
    # this point (drafting, review, risk flags) is unchanged and never skipped.
    trust_requested = request.form.get("trust_future") == "1"
    already_trusted = agent_id in TRUSTED_AGENT_IDS
    if trust_requested:
        TRUSTED_AGENT_IDS.add(agent_id)

    PENDING_AGENT_DRAFT = None

    if trust_requested or already_trusted:
        pid = _launch_agent_file(agent_id, filename)
        flash(f"Saved and started {agent_id} (PID {pid}) - trusted agent id, auto-started on approval.")
    else:
        flash(f"Saved {filename}. Read it over, then use Start when you're ready to run it.")
    return redirect(url_for("dashboard"))


@app.route("/agents/draft/discard", methods=["POST"])
@login_required
def discard_draft():
    global PENDING_AGENT_DRAFT
    token = request.form.get("token")
    if PENDING_AGENT_DRAFT and PENDING_AGENT_DRAFT["token"] == token:
        PENDING_AGENT_DRAFT = None
        flash("Draft discarded - nothing was saved or run.")
    return redirect(url_for("dashboard"))


@app.route("/agents/<agent_id>/start_custom", methods=["POST"])
@login_required
def start_custom_agent(agent_id):
    filename = APPROVED_AGENT_FILES.get(agent_id)
    if not filename or not os.path.exists(filename):
        flash(f"No approved file found for {agent_id}.")
        return redirect(url_for("dashboard"))
    pid = _launch_agent_file(agent_id, filename)
    flash(f"Started {agent_id} (PID {pid}) - it should register with the fleet shortly.")
    return redirect(url_for("dashboard"))


@app.route("/agents/<agent_id>/untrust", methods=["POST"])
@login_required
def untrust_agent(agent_id):
    TRUSTED_AGENT_IDS.discard(agent_id)
    flash(f"Revoked trust for {agent_id} - future approvals will need a separate Start click again.")
    return redirect(url_for("dashboard"))


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    now = int(time.time())
    threshold = main_agent.HEARTBEAT_THRESHOLD_SECONDS
    factory_registry = agent_factory.list_agents().get("agents", {})
    agents_report = []
    for agent_id, record in AGENTS.items():
        metrics = METRICS.get(agent_id, [])
        last_seen = record.get("last_seen")
        age_seconds = (now - int(last_seen)) if last_seen else None
        problems = main_agent.parse_agent_problems(metrics)

        if age_seconds is None:
            status = "critical"
            ratio = 1.0
        elif problems:
            status = "critical"
            ratio = min(age_seconds / threshold, 1.0)
        elif age_seconds >= threshold:
            status = "critical"
            ratio = 1.0
        elif age_seconds >= threshold * 0.5:
            status = "warning"
            ratio = age_seconds / threshold
        else:
            status = "good"
            ratio = age_seconds / threshold

        registry_entry = factory_registry.get(agent_id)

        agents_report.append({
            "agent_id": agent_id,
            "last_seen_ago": f"{age_seconds}s ago" if age_seconds is not None else "never",
            "stats": main_agent.aggregate_metrics(metrics),
            "problems": problems,
            "status": status,
            "freshness_pct": round(ratio * 100),
            "loggable": registry_entry is not None,
            "unit": registry_entry.get("unit") if registry_entry else None,
            "categories": registry_entry.get("categories") if registry_entry else [],
        })

    stale_agents = main_agent.find_stale_agents(
        [{"agent_id": aid, **record} for aid, record in AGENTS.items()],
        threshold,
    )

    repo_health_counts = {"healthy": 0, "attention": 0, "in_progress": 0, "no_workflow": 0}
    for repo in (_GITHUB_CACHE.get("repos") or []):
        state = repo.get("health", {}).get("state", "no_workflow")
        key = state if state in repo_health_counts else "attention"
        repo_health_counts[key] += 1

    pending_draft_view = None
    if PENDING_AGENT_DRAFT:
        risk_flags = _scan_code_risks(PENDING_AGENT_DRAFT["code"])
        pending_draft_view = {
            **PENDING_AGENT_DRAFT,
            "risk_flags": risk_flags,
            "code_preview_html": _render_code_preview_html(PENDING_AGENT_DRAFT["code"], risk_flags),
        }

    # Once a trusted agent auto-starts (or any approved agent is started and has
    # registered), drop it from the "not yet running" list rather than offering Start again.
    not_yet_running = {aid: fname for aid, fname in APPROVED_AGENT_FILES.items() if aid not in AGENTS}

    return render_template(
        "full_dashboard.html",
        agents=agents_report,
        stale_agents=stale_agents,
        agents_online=sum(1 for a in agents_report if a["status"] != "critical"),
        agents_total=len(agents_report),
        github=_GITHUB_CACHE,
        github_history=_GITHUB_HISTORY,
        repo_health_counts=repo_health_counts,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        pending_draft=pending_draft_view,
        approved_agent_files=not_yet_running,
        trusted_agent_ids=TRUSTED_AGENT_IDS,
    )


if __name__ == "__main__":
    refresh_github_cache()
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler()
        _scheduler.add_job(refresh_github_cache, "interval", seconds=GITHUB_REFRESH_SECONDS)
        _scheduler.start()
    except Exception as exc:
        logger.warning("Could not start GitHub refresh scheduler: %s", exc)

    # Defaults to localhost-only now that /dashboard can trigger actions. Set
    # INGESTION_BIND_HOST=0.0.0.0 explicitly if you need LAN/phone access, or when running
    # on a PaaS like Render that always needs 0.0.0.0. PORT is set automatically by Render
    # (and similar platforms) - falls back to 5000 for local/Docker use.
    bind_host = os.environ.get("INGESTION_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=bind_host, port=port, debug=False)
