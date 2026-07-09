#!/usr/bin/env python3
"""
synapse_agent.py — Multi-Agent Orchestration & Reporting System
================================================================
A self-contained system that manages multiple subordinate AI agents,
continuously collects their performance metrics, compiles those metrics
into rich HTML reports, and emails them on a configurable schedule.

Architecture:
  OrchestratorAgent  — main agent running a Claude-powered agentic loop
    ├── OcrAgent       — processes receipt images via Claude Vision
    ├── EmailAgent     — delivers outbound emails via Resend or SMTP
    └── AnalyticsAgent — analyzes sales patterns via Claude

Install dependencies:
  pip install anthropic schedule

Run in demo mode (no email or image required):
  python synapse_agent.py --demo

Run with scheduled email reports:
  ANTHROPIC_API_KEY=sk-...  RESEND_API_KEY=re_...  \\
  REPORT_RECIPIENTS=you@example.com  python synapse_agent.py

Environment variables:
  ANTHROPIC_API_KEY     Required. Powers all Claude AI capabilities.
  RESEND_API_KEY        For email via Resend API (preferred).
  SMTP_HOST             Alternative SMTP server (e.g. smtp.gmail.com).
  SMTP_PORT             SMTP port (default: 587).
  SMTP_USER             SMTP username / sender address.
  SMTP_PASS             SMTP password.
  REPORT_RECIPIENTS     Comma-separated recipient emails.
  REPORT_HOUR           UTC hour for daily report (default: 7).
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import anthropic
import schedule
from agent_sdk import AgentSDK

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-20s] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("synapse")

INGESTION_URL = os.environ.get("INGESTION_URL")
INGESTION_API_KEY = os.environ.get("INGESTION_API_KEY")
INGESTION_API_KEY_HEADER = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
AGENT_CONTROL_URL = os.environ.get("AGENT_CONTROL_URL")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    success: bool
    latency_ms: int
    data: Any = None
    error: Optional[str] = None


@dataclass
class AgentMetrics:
    agent_id: str
    agent_name: str
    agent_type: str
    status: str                         # HEALTHY | DEGRADED | IDLE
    timestamp: str
    uptime_seconds: int
    tasks_completed: int
    tasks_failed: int
    tasks_in_progress: int
    success_rate: float
    error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    last_task_at: Optional[str]
    custom_metrics: dict[str, Any]


@dataclass
class Anomaly:
    agent_id: str
    agent_name: str
    metric: str
    current_value: float
    threshold: float
    severity: str                       # warning | critical
    description: str


# ---------------------------------------------------------------------------
# AgentBase — abstract base all agents inherit from
# ---------------------------------------------------------------------------

class AgentBase(ABC):
    """
    Tracks task execution metrics automatically via _track_task().
    All agents inherit from this and call _track_task(fn) instead of fn() directly.
    """

    def __init__(self, agent_id: str, agent_name: str, agent_type: str) -> None:
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.agent_type = agent_type
        self.version = "1.0.0"
        self._started_at = datetime.now(timezone.utc)
        self._tasks_completed = 0
        self._tasks_failed = 0
        self._tasks_in_progress = 0
        self._latencies: list[int] = []     # rolling 500-sample window
        self._last_task_at: Optional[datetime] = None
        self._status = "HEALTHY"
        self._custom_metrics: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _track_task(self, fn) -> TaskResult:
        """Wraps any callable — records latency and success/failure automatically."""
        start_ns = time.monotonic_ns()
        with self._lock:
            self._tasks_in_progress += 1
        try:
            data = fn()
            latency_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
            with self._lock:
                self._tasks_completed += 1
                self._latencies.append(latency_ms)
                if len(self._latencies) > 500:
                    self._latencies.pop(0)
                self._last_task_at = datetime.now(timezone.utc)
                self._refresh_status()
            return TaskResult(success=True, latency_ms=latency_ms, data=data)
        except Exception as exc:
            latency_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
            with self._lock:
                self._tasks_failed += 1
                self._last_task_at = datetime.now(timezone.utc)
                self._refresh_status()
            log.warning("[%s] Task failed (%d ms): %s", self.agent_name, latency_ms, exc)
            return TaskResult(success=False, latency_ms=latency_ms, error=str(exc))
        finally:
            with self._lock:
                self._tasks_in_progress -= 1

    def _refresh_status(self) -> None:
        total = self._tasks_completed + self._tasks_failed
        if total == 0:
            return
        self._status = "DEGRADED" if (self._tasks_failed / total) > 0.5 else "HEALTHY"

    def _p95(self) -> float:
        if not self._latencies:
            return 0.0
        s = sorted(self._latencies)
        return float(s[min(int(len(s) * 0.95), len(s) - 1)])

    def _avg(self) -> float:
        return statistics.mean(self._latencies) if self._latencies else 0.0

    def get_metrics(self) -> AgentMetrics:
        with self._lock:
            total = self._tasks_completed + self._tasks_failed
            uptime = int((datetime.now(timezone.utc) - self._started_at).total_seconds())
            return AgentMetrics(
                agent_id=self.agent_id,
                agent_name=self.agent_name,
                agent_type=self.agent_type,
                status=self._status,
                timestamp=datetime.now(timezone.utc).isoformat(),
                uptime_seconds=uptime,
                tasks_completed=self._tasks_completed,
                tasks_failed=self._tasks_failed,
                tasks_in_progress=self._tasks_in_progress,
                success_rate=self._tasks_completed / total if total > 0 else 1.0,
                error_rate=self._tasks_failed / total if total > 0 else 0.0,
                avg_latency_ms=round(self._avg(), 1),
                p95_latency_ms=round(self._p95(), 1),
                last_task_at=self._last_task_at.isoformat() if self._last_task_at else None,
                custom_metrics=dict(self._custom_metrics),
            )

    def heartbeat(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self._status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @abstractmethod
    def description(self) -> str: ...


# ---------------------------------------------------------------------------
# AgentRegistry — singleton, holds all registered agents
# ---------------------------------------------------------------------------

class _AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentBase] = {}

    def register(self, agent: AgentBase) -> None:
        self._agents[agent.agent_id] = agent
        log.info("[Registry] Registered: %s  (%s)", agent.agent_name, agent.agent_id)

    def get(self, agent_id: str) -> Optional[AgentBase]:
        return self._agents.get(agent_id)

    def all(self) -> list[AgentBase]:
        return list(self._agents.values())

    def collect_all_metrics(self) -> list[AgentMetrics]:
        return [a.get_metrics() for a in self.all()]

    def __len__(self) -> int:
        return len(self._agents)


registry = _AgentRegistry()


def _is_ingestion_enabled() -> bool:
    return bool(INGESTION_URL)


def _agent_ingestion_client(agent: AgentBase) -> AgentSDK:
    return AgentSDK(
        agent_id=agent.agent_id,
        ingestion_url=INGESTION_URL,
        tags={"agent_type": agent.agent_type, "agent_name": agent.agent_name},
        api_key=INGESTION_API_KEY,
        api_key_header=INGESTION_API_KEY_HEADER,
    )


def _build_agent_info(agent: AgentBase) -> dict:
    info = {
        "name": agent.agent_name,
        "type": agent.agent_type,
        "description": agent.description(),
        "version": agent.version,
    }
    if AGENT_CONTROL_URL:
        info["control_url"] = AGENT_CONTROL_URL
    return info


def _build_agent_metrics(agent: AgentBase) -> dict:
    metrics = agent.get_metrics()
    return {
        "status": metrics.status,
        "uptime_seconds": metrics.uptime_seconds,
        "tasks_completed": metrics.tasks_completed,
        "tasks_failed": metrics.tasks_failed,
        "tasks_in_progress": metrics.tasks_in_progress,
        "success_rate": metrics.success_rate,
        "error_rate": metrics.error_rate,
        "avg_latency_ms": metrics.avg_latency_ms,
        "p95_latency_ms": metrics.p95_latency_ms,
        "last_task_at": metrics.last_task_at,
        "custom_metrics": metrics.custom_metrics,
    }


def _publish_agent_to_ingestion(agent: AgentBase) -> None:
    if not _is_ingestion_enabled():
        return
    try:
        sdk = _agent_ingestion_client(agent)
        sdk.register(_build_agent_info(agent))
        sdk.send_metrics(_build_agent_metrics(agent))
        log.debug("Published ingestion data for %s", agent.agent_id)
    except Exception as exc:
        log.warning("Failed to publish ingestion data for %s: %s", agent.agent_id, exc)


# ---------------------------------------------------------------------------
# OcrAgent — extracts payment data from receipt images via Claude Vision
# ---------------------------------------------------------------------------

class OcrAgent(AgentBase):
    _PROMPT = (
        "Analyze this sales receipt image. Return ONLY valid JSON with these keys:\n"
        "cashPayments: array of {description, amount},\n"
        "cardPayments: array of {description, amount},\n"
        "confidence: 'high'|'medium'|'low',\n"
        "notes: string.\n"
        "Do not include any text outside the JSON object."
    )

    def __init__(self) -> None:
        super().__init__("ocr-agent-001", "OCR Agent", "ocr")
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._confidence_tally: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        self._tokens_used = 0

    def description(self) -> str:
        return "Extracts structured payment data from receipt images using Claude Vision."

    def process_image(self, base64_image: str, mime_type: str = "image/jpeg") -> TaskResult:
        """Extract cash/card payments from a base64-encoded receipt image."""
        def _run():
            resp = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": mime_type, "data": base64_image,
                        }},
                        {"type": "text", "text": self._PROMPT},
                    ],
                }],
            )
            raw = next(b.text for b in resp.content if b.type == "text")
            result = json.loads(raw)
            conf = result.get("confidence", "low")
            self._confidence_tally[conf] = self._confidence_tally.get(conf, 0) + 1
            self._tokens_used += resp.usage.input_tokens + resp.usage.output_tokens
            self._custom_metrics.update({
                "tokens_used": self._tokens_used,
                "high_confidence": self._confidence_tally["high"],
                "medium_confidence": self._confidence_tally["medium"],
                "low_confidence": self._confidence_tally["low"],
                "est_cost_usd": round((self._tokens_used / 1_000_000) * 3.0, 6),
            })
            return result

        return self._track_task(_run)

    def simulate_task(self) -> TaskResult:
        """Simulates an OCR task with realistic random latency and occasional failures."""
        import random

        def _run():
            time.sleep(random.uniform(0.2, 1.5))
            if random.random() < 0.05:
                raise RuntimeError("Simulated: image resolution too low")
            conf = random.choices(["high", "medium", "low"], weights=[70, 20, 10])[0]
            self._confidence_tally[conf] += 1
            self._tokens_used += random.randint(200, 600)
            self._custom_metrics.update({
                "tokens_used": self._tokens_used,
                "high_confidence": self._confidence_tally["high"],
                "medium_confidence": self._confidence_tally["medium"],
                "low_confidence": self._confidence_tally["low"],
                "est_cost_usd": round((self._tokens_used / 1_000_000) * 3.0, 6),
            })
            return {"confidence": conf, "cashPayments": [], "cardPayments": []}

        return self._track_task(_run)


# ---------------------------------------------------------------------------
# EmailAgent — delivers emails via Resend API or SMTP
# ---------------------------------------------------------------------------

class EmailAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__("email-agent-001", "Email Agent", "email")
        self._resend_key = os.environ.get("RESEND_API_KEY")
        self._smtp_host = os.environ.get("SMTP_HOST")
        self._smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self._smtp_user = os.environ.get("SMTP_USER", "")
        self._smtp_pass = os.environ.get("SMTP_PASS", "")
        self._emails_sent = 0
        self._bytes_sent = 0

    def description(self) -> str:
        return "Delivers HTML emails via Resend API or SMTP."

    def send(self, to: list[str], subject: str, html: str, text: str = "") -> TaskResult:
        def _run():
            if self._resend_key:
                self._via_resend(to, subject, html)
            elif self._smtp_host:
                self._via_smtp(to, subject, html, text)
            else:
                raise RuntimeError(
                    "No email transport configured. Set RESEND_API_KEY or SMTP_HOST/USER/PASS."
                )
            self._emails_sent += 1
            self._bytes_sent += len(html.encode())
            self._custom_metrics["emails_sent"] = self._emails_sent
            self._custom_metrics["kb_delivered"] = round(self._bytes_sent / 1024, 1)
            return {"delivered_to": to}

        return self._track_task(_run)

    def _via_resend(self, to: list[str], subject: str, html: str) -> None:
        payload = json.dumps({
            "from": "Synapse <onboarding@resend.dev>",
            "to": to,
            "subject": subject,
            "html": html,
        }).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._resend_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            if "error" in body:
                raise RuntimeError(f"Resend: {body['error']}")

    def _via_smtp(self, to: list[str], subject: str, html: str, text: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._smtp_user
        msg["To"] = ", ".join(to)
        if text:
            msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(self._smtp_user, self._smtp_pass)
            srv.sendmail(self._smtp_user, to, msg.as_string())

    def simulate_task(self) -> TaskResult:
        import random

        def _run():
            time.sleep(random.uniform(0.05, 0.3))
            if random.random() < 0.03:
                raise RuntimeError("Simulated: SMTP connection refused")
            self._emails_sent += 1
            self._custom_metrics["emails_sent"] = self._emails_sent

        return self._track_task(_run)


# ---------------------------------------------------------------------------
# AnalyticsAgent — uses Claude to surface patterns in sales data
# ---------------------------------------------------------------------------

class AnalyticsAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__("analytics-agent-001", "Analytics Agent", "analytics")
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._tokens_used = 0
        self._analyses_run = 0

    def description(self) -> str:
        return "Identifies sales patterns, trends, and anomalies using Claude."

    def analyze(self, data: list[dict], context: str = "") -> TaskResult:
        """Run Claude analysis over a list of structured sales records."""
        def _run():
            prompt = (
                f"You are a sales analytics expert. {context}\n\n"
                "Analyze the following sales data and return ONLY a JSON object with:\n"
                "- summary: 2-3 sentence executive summary\n"
                "- top_insights: list of 3 key findings\n"
                "- anomalies: list of anything unusual\n"
                "- recommendations: list of 2 actionable suggestions\n\n"
                f"Data:\n{json.dumps(data)}"
            )
            resp = self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = next(b.text for b in resp.content if b.type == "text")
            result = json.loads(raw)
            self._tokens_used += resp.usage.input_tokens + resp.usage.output_tokens
            self._analyses_run += 1
            self._custom_metrics["analyses_run"] = self._analyses_run
            self._custom_metrics["tokens_used"] = self._tokens_used
            return result

        return self._track_task(_run)

    def simulate_task(self) -> TaskResult:
        import random

        def _run():
            time.sleep(random.uniform(0.3, 2.0))
            if random.random() < 0.04:
                raise RuntimeError("Simulated: model context window exceeded")
            self._analyses_run += 1
            self._tokens_used += random.randint(300, 900)
            self._custom_metrics["analyses_run"] = self._analyses_run
            self._custom_metrics["tokens_used"] = self._tokens_used
            return {"summary": "Demo analysis complete", "top_insights": [], "anomalies": []}

        return self._track_task(_run)


# ---------------------------------------------------------------------------
# OrchestratorAgent — main agent with Claude-powered agentic tool-use loop
# ---------------------------------------------------------------------------

class OrchestratorAgent(AgentBase):
    """
    The central brain of Synapse.

    When run_report_cycle() is called it starts a multi-turn Claude conversation
    where Claude decides which tools to call (collect metrics → detect anomalies →
    compile report → send email) and processes the results at each turn until the
    report is complete and delivered.
    """

    # Tool definitions exposed to Claude
    _TOOLS: list[dict] = [
        {
            "name": "get_all_agent_metrics",
            "description": "Retrieve live performance metrics from every registered subordinate agent.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "detect_anomalies",
            "description": (
                "Scan a list of agent metrics for anomalies. Flag error_rate > 10% as warning, "
                "> 30% as critical. Flag p95_latency_ms > 5000 as warning. "
                "Flag agents with zero completed tasks after 2+ minutes as warning."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "metrics": {"type": "array", "description": "List of agent metric objects"},
                },
                "required": ["metrics"],
            },
        },
        {
            "name": "compile_fleet_report",
            "description": "Assemble the final FleetReport from metrics, anomalies, and AI analysis text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "metrics": {"type": "array"},
                    "anomalies": {"type": "array"},
                    "ai_analysis": {
                        "type": "string",
                        "description": "2–3 sentence narrative summary you write about overall fleet health",
                    },
                    "period_start": {"type": "string", "description": "ISO 8601 start of period"},
                    "period_end": {"type": "string", "description": "ISO 8601 end of period"},
                },
                "required": ["metrics", "anomalies", "ai_analysis", "period_start", "period_end"],
            },
        },
        {
            "name": "send_report_email",
            "description": "Email the compiled fleet performance report to all configured recipients.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "report": {"type": "object", "description": "The compiled FleetReport object"},
                    "recipients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Recipient email addresses",
                    },
                },
                "required": ["report", "recipients"],
            },
        },
    ]

    def __init__(
        self,
        email_agent: EmailAgent,
        recipients: list[str],
        report_hour: int = 7,
        demo_mode: bool = False,
    ) -> None:
        super().__init__("orchestrator-001", "Orchestrator", "orchestrator")
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._email_agent = email_agent
        self._recipients = recipients
        self._report_hour = report_hour
        self._demo_mode = demo_mode
        self._period_start = datetime.now(timezone.utc).isoformat()
        self._last_report: Optional[dict] = None

    def description(self) -> str:
        return "Orchestrates subordinate agents, collects metrics, and delivers scheduled reports."

    # ------------------------------------------------------------------
    # Tool implementations (called when Claude requests each tool)
    # ------------------------------------------------------------------

    def _tool_get_all_agent_metrics(self, _inputs: dict) -> list[dict]:
        metrics = registry.collect_all_metrics()
        return [
            {
                "agent_id": m.agent_id,
                "agent_name": m.agent_name,
                "agent_type": m.agent_type,
                "status": m.status,
                "uptime_seconds": m.uptime_seconds,
                "tasks_completed": m.tasks_completed,
                "tasks_failed": m.tasks_failed,
                "success_rate": round(m.success_rate, 4),
                "error_rate": round(m.error_rate, 4),
                "avg_latency_ms": m.avg_latency_ms,
                "p95_latency_ms": m.p95_latency_ms,
                "last_task_at": m.last_task_at,
                "custom_metrics": m.custom_metrics,
            }
            for m in metrics
        ]

    def _tool_detect_anomalies(self, inputs: dict) -> list[dict]:
        anomalies = []
        for m in inputs.get("metrics", []):
            error_rate = m.get("error_rate", 0)
            p95 = m.get("p95_latency_ms", 0)
            completed = m.get("tasks_completed", 0)
            uptime = m.get("uptime_seconds", 0)

            if error_rate > 0.1:
                anomalies.append({
                    "agent_id": m["agent_id"],
                    "agent_name": m["agent_name"],
                    "metric": "error_rate",
                    "current_value": error_rate,
                    "threshold": 0.1,
                    "severity": "critical" if error_rate > 0.3 else "warning",
                    "description": f"Error rate {error_rate:.1%} exceeds 10% threshold",
                })
            if p95 > 5000:
                anomalies.append({
                    "agent_id": m["agent_id"],
                    "agent_name": m["agent_name"],
                    "metric": "p95_latency_ms",
                    "current_value": p95,
                    "threshold": 5000,
                    "severity": "warning",
                    "description": f"P95 latency {p95:.0f} ms exceeds 5,000 ms threshold",
                })
            if completed == 0 and uptime > 120:
                anomalies.append({
                    "agent_id": m["agent_id"],
                    "agent_name": m["agent_name"],
                    "metric": "tasks_completed",
                    "current_value": 0,
                    "threshold": 1,
                    "severity": "warning",
                    "description": "Agent has been running >2 min with no completed tasks",
                })
        return anomalies

    def _tool_compile_fleet_report(self, inputs: dict) -> dict:
        raw = inputs["metrics"]
        anomalies = inputs["anomalies"]
        now = datetime.now(timezone.utc).isoformat()

        healthy = sum(1 for m in raw if m.get("status") == "HEALTHY")
        degraded = sum(1 for m in raw if m.get("status") == "DEGRADED")
        total_completed = sum(m.get("tasks_completed", 0) for m in raw)
        total_failed = sum(m.get("tasks_failed", 0) for m in raw)
        grand_total = total_completed + total_failed
        fleet_error_rate = total_failed / grand_total if grand_total > 0 else 0.0
        latencies = [m.get("avg_latency_ms", 0) for m in raw if m.get("avg_latency_ms")]
        fleet_avg_latency = statistics.mean(latencies) if latencies else 0.0

        report = {
            "generated_at": now,
            "period_start": inputs.get("period_start", self._period_start),
            "period_end": inputs.get("period_end", now),
            "total_agents": len(raw),
            "healthy_agents": healthy,
            "degraded_agents": degraded,
            "unreachable_agents": len(raw) - healthy - degraded,
            "fleet_error_rate": round(fleet_error_rate, 4),
            "fleet_avg_latency_ms": round(fleet_avg_latency, 1),
            "total_tasks_completed": total_completed,
            "total_tasks_failed": total_failed,
            "agent_reports": raw,
            "anomalies": anomalies,
            "ai_analysis": inputs.get("ai_analysis", ""),
        }
        self._last_report = report
        return report

    def _tool_send_report_email(self, inputs: dict) -> dict:
        report = inputs["report"]
        recipients = inputs.get("recipients") or self._recipients

        if self._demo_mode:
            log.info("[Orchestrator] DEMO — skipping real email. Would send to: %s", recipients)
            _print_report_summary(report)
            return {"status": "skipped_in_demo_mode", "recipients": recipients}

        if not recipients:
            log.warning("[Orchestrator] No recipients configured — skipping email.")
            return {"status": "no_recipients"}

        html = _build_report_html(report)
        subject = (
            f"Synapse Fleet Report — {report['generated_at'][:10]} | "
            f"{report['total_agents']} agents | "
            f"Errors: {report['fleet_error_rate'] * 100:.1f}%"
        )
        result = self._email_agent.send(recipients, subject, html)
        if not result.success:
            raise RuntimeError(f"Email delivery failed: {result.error}")
        log.info("[Orchestrator] Report emailed to %s", recipients)
        return {"status": "sent", "recipients": recipients}

    # ------------------------------------------------------------------
    # Agentic loop — Claude drives the full reporting cycle
    # ------------------------------------------------------------------

    def run_report_cycle(self) -> Optional[dict]:
        """
        Triggers one complete reporting cycle via Claude's tool-use agentic loop.
        Claude calls tools in sequence: collect → detect → compile → send.
        """
        log.info("[Orchestrator] Starting report cycle (%d agents in registry)", len(registry))

        system = (
            "You are Synapse, an AI operations manager overseeing a fleet of software agents. "
            "Run a complete performance reporting cycle by calling these tools in order:\n"
            "1. get_all_agent_metrics — collect live data from all agents.\n"
            "2. detect_anomalies — identify problems in the metrics.\n"
            "3. compile_fleet_report — write a concise 2-3 sentence ai_analysis and assemble the report.\n"
            "4. send_report_email — deliver the final report.\n"
            "Work methodically. Do not skip any step."
        )

        period_end = datetime.now(timezone.utc).isoformat()
        messages: list[dict] = [{
            "role": "user",
            "content": (
                f"Run the full performance reporting cycle now. "
                f"Period: {self._period_start} to {period_end}. "
                f"Recipients: {self._recipients}."
            ),
        }]

        dispatch = {
            "get_all_agent_metrics": self._tool_get_all_agent_metrics,
            "detect_anomalies": self._tool_detect_anomalies,
            "compile_fleet_report": self._tool_compile_fleet_report,
            "send_report_email": self._tool_send_report_email,
        }

        final_report = None

        def _loop():
            nonlocal messages, final_report
            for turn in range(10):                          # safety cap: max 10 turns
                resp = self._client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=system,
                    tools=self._TOOLS,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": resp.content})

                if resp.stop_reason == "end_turn":
                    log.info("[Orchestrator] Agentic loop finished after %d turn(s).", turn + 1)
                    break

                if resp.stop_reason == "tool_use":
                    tool_results = []
                    for block in resp.content:
                        if block.type != "tool_use":
                            continue
                        log.info("[Orchestrator] → tool: %s", block.name)
                        try:
                            result = dispatch[block.name](block.input)
                            if block.name == "compile_fleet_report":
                                final_report = result
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result),
                            })
                        except Exception as exc:
                            log.error("[Orchestrator] Tool %s raised: %s", block.name, exc)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "is_error": True,
                                "content": str(exc),
                            })
                    messages.append({"role": "user", "content": tool_results})
                else:
                    log.warning("[Orchestrator] Unexpected stop_reason: %s", resp.stop_reason)
                    break

        result = self._track_task(_loop)
        if not result.success:
            log.error("[Orchestrator] Report cycle failed: %s", result.error)

        if _is_ingestion_enabled():
            for agent in registry.all():
                _publish_agent_to_ingestion(agent)

        self._period_start = period_end          # advance window for next cycle
        return final_report

    # ------------------------------------------------------------------
    # Scheduler — runs daily at configured UTC hour
    # ------------------------------------------------------------------

    def start_scheduler(self) -> None:
        schedule.every().day.at(f"{self._report_hour:02d}:00").do(self.run_report_cycle)
        log.info("[Orchestrator] Daily report scheduled at %02d:00 UTC", self._report_hour)

        def _runner():
            while True:
                schedule.run_pending()
                time.sleep(30)

        t = threading.Thread(target=_runner, daemon=True, name="synapse-scheduler")
        t.start()
        log.info("[Orchestrator] Scheduler thread started.")


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------

def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _status_chip(status: str) -> str:
    colors = {"HEALTHY": "#16a34a", "DEGRADED": "#d97706", "IDLE": "#6b7280"}
    c = colors.get(status, "#6b7280")
    return (
        f'<span style="background:{c};color:#fff;padding:2px 9px;'
        f'border-radius:10px;font-size:11px;font-weight:700;">{status}</span>'
    )


def _severity_chip(severity: str) -> str:
    c = "#dc2626" if severity == "critical" else "#d97706"
    return (
        f'<span style="background:{c};color:#fff;padding:2px 9px;'
        f'border-radius:10px;font-size:11px;font-weight:700;">{severity.upper()}</span>'
    )


def _agent_rows_html(agent_reports: list[dict]) -> str:
    rows = []
    for m in agent_reports:
        err_color = "#dc2626" if m["error_rate"] > 0.1 else "#374151"
        uptime_h = m["uptime_seconds"] // 3600
        uptime_m = (m["uptime_seconds"] % 3600) // 60
        rows.append(
            f'<tr style="border-bottom:1px solid #f1f5f9;">'
            f'<td style="padding:11px 14px;font-size:13px;font-weight:600;color:#0f172a;">{m["agent_name"]}</td>'
            f'<td style="padding:11px 14px;">{_status_chip(m["status"])}</td>'
            f'<td style="padding:11px 14px;font-size:13px;color:#374151;">{m["tasks_completed"]:,}</td>'
            f'<td style="padding:11px 14px;font-size:13px;color:{err_color};">{_pct(m["error_rate"])}</td>'
            f'<td style="padding:11px 14px;font-size:13px;color:#374151;">{m["avg_latency_ms"]:.0f}</td>'
            f'<td style="padding:11px 14px;font-size:13px;color:#374151;">{m["p95_latency_ms"]:.0f}</td>'
            f'<td style="padding:11px 14px;font-size:13px;color:#374151;">{uptime_h}h {uptime_m}m</td>'
            f'</tr>'
        )
    return "".join(rows)


def _anomaly_rows_html(anomalies: list[dict]) -> str:
    if not anomalies:
        return (
            '<tr><td colspan="4" style="padding:12px 14px;color:#6b7280;'
            'font-style:italic;font-size:13px;">No anomalies detected</td></tr>'
        )
    rows = []
    for a in anomalies:
        rows.append(
            f'<tr style="border-bottom:1px solid #fef3c7;">'
            f'<td style="padding:10px 14px;font-size:13px;font-weight:600;color:#0f172a;">{a["agent_name"]}</td>'
            f'<td style="padding:10px 14px;">{_severity_chip(a["severity"])}</td>'
            f'<td style="padding:10px 14px;font-size:13px;color:#374151;">{a["metric"]}</td>'
            f'<td style="padding:10px 14px;font-size:13px;color:#374151;">{a["description"]}</td>'
            f'</tr>'
        )
    return "".join(rows)


def _build_report_html(report: dict) -> str:
    generated = report["generated_at"][:19].replace("T", " ") + " UTC"
    healthy = report["healthy_agents"]
    total = report["total_agents"]
    all_ok = healthy == total
    banner_color = "#16a34a" if all_ok else "#d97706"
    banner_label = "ALL SYSTEMS HEALTHY" if all_ok else f"{report['degraded_agents']} AGENT(S) DEGRADED"
    anomaly_count = len(report["anomalies"])
    anomaly_badge = (
        f' <span style="background:#dc2626;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px;">'
        f'{anomaly_count}</span>'
        if anomaly_count else ""
    )
    border_color = "#fde68a" if anomaly_count else "#e2e8f0"
    header_bg = "#fffbeb" if anomaly_count else "#f8fafc"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Synapse Fleet Report</title>
</head>
<body style="margin:0;padding:0;background:#eef2f7;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;padding:40px 16px;">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">

  <tr>
    <td style="background:#0f172a;border-radius:12px 12px 0 0;padding:10px 28px;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="color:#94a3b8;font-size:11px;letter-spacing:1px;text-transform:uppercase;font-weight:600;">Synapse · Fleet Report</td>
        <td align="right" style="color:#475569;font-size:11px;">{generated}</td>
      </tr></table>
    </td>
  </tr>

  <tr>
    <td style="background:linear-gradient(160deg,#1e1b4b 0%,#312e81 55%,#4338ca 100%);padding:40px 36px;text-align:center;">
      <div style="font-size:44px;margin-bottom:14px;">🧠</div>
      <h1 style="margin:0 0 8px;color:#fff;font-size:26px;font-weight:700;letter-spacing:-0.5px;">Agent Fleet Report</h1>
      <p style="margin:0;color:#a5b4fc;font-size:14px;">
        {report['period_start'][:10]} &rarr; {report['period_end'][:10]}
      </p>
    </td>
  </tr>

  <tr>
    <td style="background:{banner_color};padding:11px 28px;text-align:center;">
      <span style="color:#fff;font-size:13px;font-weight:700;letter-spacing:0.5px;">{banner_label}</span>
    </td>
  </tr>

  <tr>
    <td style="background:#1e1b4b;padding:4px 18px 22px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="padding-top:20px;"><tr>
        <td width="25%" style="padding:4px;">
          <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:16px 10px;text-align:center;">
            <div style="color:#a5b4fc;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Agents</div>
            <div style="color:#fff;font-size:26px;font-weight:700;">{healthy}<span style="font-size:16px;color:#94a3b8;">/{total}</span></div>
          </div>
        </td>
        <td width="25%" style="padding:4px;">
          <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:16px 10px;text-align:center;">
            <div style="color:#a5b4fc;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Tasks Done</div>
            <div style="color:#fff;font-size:26px;font-weight:700;">{report['total_tasks_completed']:,}</div>
          </div>
        </td>
        <td width="25%" style="padding:4px;">
          <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:16px 10px;text-align:center;">
            <div style="color:#a5b4fc;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Error Rate</div>
            <div style="color:{'#f87171' if report['fleet_error_rate'] > 0.05 else '#34d399'};font-size:26px;font-weight:700;">{_pct(report['fleet_error_rate'])}</div>
          </div>
        </td>
        <td width="25%" style="padding:4px;">
          <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:16px 10px;text-align:center;">
            <div style="color:#a5b4fc;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Avg Latency</div>
            <div style="color:#fff;font-size:26px;font-weight:700;">{report['fleet_avg_latency_ms']:.0f}<span style="font-size:14px;color:#94a3b8;">ms</span></div>
          </div>
        </td>
      </tr></table>
    </td>
  </tr>

  <tr>
    <td style="background:#fff;padding:32px 36px;">

      <div style="margin-bottom:28px;">
        <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px;">AI Analysis</div>
        <div style="background:#f8fafc;border-left:4px solid #4338ca;border-radius:0 8px 8px 0;padding:14px 18px;font-size:14px;color:#374151;line-height:1.75;">
          {report['ai_analysis'] or 'No analysis generated.'}
        </div>
      </div>

      <div style="margin-bottom:28px;">
        <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:12px;">Agent Performance</div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:8px;overflow:hidden;border:1px solid #e2e8f0;">
          <tr style="background:#f8fafc;">
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Agent</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Status</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Done</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Err%</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Avg ms</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">P95 ms</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Uptime</th>
          </tr>
          {_agent_rows_html(report['agent_reports'])}
        </table>
      </div>

      <div>
        <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:12px;">
          Anomalies &amp; Alerts{anomaly_badge}
        </div>
        <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:8px;overflow:hidden;border:1px solid {border_color};">
          <tr style="background:{header_bg};">
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Agent</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Severity</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Metric</th>
            <th style="padding:9px 14px;text-align:left;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Details</th>
          </tr>
          {_anomaly_rows_html(report['anomalies'])}
        </table>
      </div>

    </td>
  </tr>

  <tr>
    <td style="background:#f8fafc;border-top:1px solid #e2e8f0;border-radius:0 0 12px 12px;padding:14px 36px;text-align:center;">
      <p style="margin:0;font-size:12px;color:#94a3b8;">
        Generated by <strong style="color:#4338ca;">Synapse</strong> · Multi-Agent Orchestration System
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Console report summary (used in demo mode)
# ---------------------------------------------------------------------------

def _print_report_summary(report: dict) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print("  SYNAPSE FLEET REPORT")
    print(f"  Generated: {report['generated_at'][:19]} UTC")
    print(sep)
    print(f"  Agents:       {report['healthy_agents']}/{report['total_agents']} healthy")
    print(f"  Tasks done:   {report['total_tasks_completed']:,}")
    print(f"  Tasks failed: {report['total_tasks_failed']:,}")
    print(f"  Fleet errors: {_pct(report['fleet_error_rate'])}")
    print(f"  Avg latency:  {report['fleet_avg_latency_ms']:.1f} ms")
    print()
    print("  AGENTS")
    for m in report["agent_reports"]:
        print(f"  [{m['status']:<9}] {m['agent_name']:<20} "
              f"done={m['tasks_completed']}  err={_pct(m['error_rate'])}  "
              f"avg={m['avg_latency_ms']:.0f}ms")
    print()
    if report["anomalies"]:
        print(f"  ANOMALIES ({len(report['anomalies'])})")
        for a in report["anomalies"]:
            print(f"  [{a['severity'].upper():<8}] {a['agent_name']}: {a['description']}")
    else:
        print("  ANOMALIES: none")
    print()
    print(f"  AI ANALYSIS")
    print(f"  {report['ai_analysis']}")
    print(sep + "\n")


# ---------------------------------------------------------------------------
# Demo mode — simulates workloads then triggers an immediate report cycle
# ---------------------------------------------------------------------------

def run_demo(
    orchestrator: OrchestratorAgent,
    ocr: OcrAgent,
    email_agent: EmailAgent,
    analytics: AnalyticsAgent,
) -> None:
    log.info("=== DEMO MODE: simulating agent workloads ===")

    import random

    def work_ocr():
        for _ in range(12):
            ocr.simulate_task()
            time.sleep(random.uniform(0.05, 0.15))

    def work_email():
        for _ in range(8):
            email_agent.simulate_task()
            time.sleep(random.uniform(0.03, 0.1))

    def work_analytics():
        for _ in range(6):
            analytics.simulate_task()
            time.sleep(random.uniform(0.1, 0.25))

    threads = [
        threading.Thread(target=work_ocr, daemon=True),
        threading.Thread(target=work_email, daemon=True),
        threading.Thread(target=work_analytics, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log.info("Simulated workloads done. Triggering report cycle via agentic loop...")
    orchestrator.run_report_cycle()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    demo_mode = "--demo" in sys.argv

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is not set. Export it and re-run.")
        sys.exit(1)

    recipients_raw = os.environ.get("REPORT_RECIPIENTS", "")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients and not demo_mode:
        log.warning("REPORT_RECIPIENTS not set — email delivery will be skipped.")

    report_hour = int(os.environ.get("REPORT_HOUR", "7"))

    # Instantiate all agents
    ocr = OcrAgent()
    email_agent = EmailAgent()
    analytics = AnalyticsAgent()
    orchestrator = OrchestratorAgent(
        email_agent=email_agent,
        recipients=recipients,
        report_hour=report_hour,
        demo_mode=demo_mode,
    )

    # Register subordinate agents with the shared registry
    for agent in [ocr, email_agent, analytics, orchestrator]:
        registry.register(agent)

    if _is_ingestion_enabled():
        log.info("Publishing initial agent registrations to ingestion server %s", INGESTION_URL)
        for agent in registry.all():
            _publish_agent_to_ingestion(agent)

    log.info("Synapse ready. %d agents registered.", len(registry))

    if demo_mode:
        run_demo(orchestrator, ocr, email_agent, analytics)
        return

    # Production: start the daily scheduler and block
    orchestrator.start_scheduler()
    log.info("Running. Daily report at %02d:00 UTC. Press Ctrl+C to stop.", report_hour)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Synapse shutting down.")


if __name__ == "__main__":
    main()
