"""Canvas Tutor adapter for publishing private agent events to Synapse ingestion.

This example shows how a private Canvas Tutor agent can register itself with the
Synapse ingestion server, send periodic telemetry updates, and report
problems so the MainAgent can include it in fleet dashboards and alerts.
"""

import os
import time
import socket
from typing import Any

from agent_sdk import AgentSDK
from canvas_oauth import CanvasOAuthClient, CanvasOAuthError


class CanvasTutorAdapter:
    def __init__(self) -> None:
        self.agent_id = os.environ.get("CANVAS_TUTOR_AGENT_ID", "canvas-tutor-agent")
        self.ingestion_url = os.environ.get("INGESTION_URL", "http://localhost:5000")
        self.api_key = os.environ.get("INGESTION_API_KEY")
        self.api_key_header = os.environ.get("INGESTION_API_KEY_HEADER", "X-API-KEY")
        self.control_url = os.environ.get("AGENT_CONTROL_URL")
        self.default_latency_ms = int(os.environ.get("CANVAS_TUTOR_LATENCY_MS", "0"))
        self.session_count = int(os.environ.get("CANVAS_TUTOR_SESSION_COUNT", "0"))
        self.sdk = AgentSDK(
            agent_id=self.agent_id,
            ingestion_url=self.ingestion_url,
            tags={"agent_type": "canvas_tutor", "service": "Canvas Tutor"},
            api_key=self.api_key,
            api_key_header=self.api_key_header,
        )

    def _build_info(self) -> dict[str, Any]:
        info = {
            "name": "Canvas Tutor",
            "type": "canvas_tutor",
            "description": "Private Canvas Tutor agent integrated into Synapse.",
            "version": os.environ.get("CANVAS_TUTOR_VERSION", "1.0.0"),
            "host": socket.gethostname(),
        }
        if self.control_url:
            info["control_url"] = self.control_url
        canvas_base_url = os.environ.get("CANVAS_BASE_URL")
        if canvas_base_url:
            info["canvas_base_url"] = canvas_base_url.rstrip("/")
            info["canvas_oauth_start_url"] = os.environ.get(
                "CANVAS_OAUTH_START_URL",
                "http://localhost:8080/canvas/oauth/start",
            )
        return info

    def _build_metrics(self, success: bool, latency_ms: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        metrics = {
            "status": "HEALTHY" if success else "DEGRADED",
            "latency_ms": latency_ms,
            "tasks_completed": 1 if success else 0,
            "tasks_failed": 0 if success else 1,
            "last_event": int(time.time()),
        }
        if extra:
            metrics.update(extra)
        return metrics

    def register(self) -> None:
        """Register the Canvas Tutor agent with the ingestion server."""
        info = self._build_info()
        self.sdk.register(info)
        print(f"Registered Canvas Tutor agent {self.agent_id} to {self.ingestion_url}")

    def publish_telemetry(self, success: bool, latency_ms: int, extra: dict[str, Any] | None = None) -> None:
        """Send a telemetry update for the Canvas Tutor agent."""
        metrics = self._build_metrics(success=success, latency_ms=latency_ms, extra=extra)
        self.sdk.send_metrics(metrics)
        print(f"Published telemetry for {self.agent_id}: status={metrics['status']}")

    def report_problem(self, message: str, severity: str = "critical", details: dict[str, Any] | None = None) -> None:
        """Send a structured problem report so MainAgent can surface alerts."""
        self.sdk.report_problem(message=message, severity=severity, details=details)
        print(f"Reported problem for {self.agent_id}: {message}")

    def _canvas_oauth_metrics(self) -> tuple[bool, dict[str, Any]]:
        """Return Canvas OAuth readiness without exposing token values."""
        if not os.environ.get("CANVAS_BASE_URL"):
            return True, {"canvas_oauth": "not_configured"}

        try:
            client = CanvasOAuthClient.from_env(require_client_credentials=False)
            client.get_valid_access_token()
            status = client.token_status()
            metrics: dict[str, Any] = {
                "canvas_oauth": "ready",
                "canvas_base_url": status.get("base_url"),
                "canvas_token_expires_in_seconds": status.get("expires_in_seconds"),
                "canvas_token_using_env": status.get("using_env_access_token", False),
            }

            if os.environ.get("CANVAS_TUTOR_VERIFY_API", "").lower() in {"1", "true", "yes"}:
                profile = client.api_get("/api/v1/users/self/profile").json()
                metrics["canvas_user_id"] = profile.get("id")
                metrics["canvas_user_name"] = profile.get("name")

            return True, metrics
        except CanvasOAuthError as exc:
            return False, {"canvas_oauth": "error", "canvas_error": str(exc)}
        except Exception as exc:
            return False, {"canvas_oauth": "error", "canvas_error": str(exc)}

    def publish_heartbeat(self) -> None:
        """Publish a healthy heartbeat without creating a fake problem alert."""
        canvas_ok, canvas_metrics = self._canvas_oauth_metrics()
        self.publish_telemetry(
            success=canvas_ok,
            latency_ms=self.default_latency_ms,
            extra={"session_count": self.session_count, **canvas_metrics},
        )

    def run_heartbeat_loop(self, interval_seconds: int) -> None:
        """Register once, then publish healthy telemetry until the process stops."""
        self.register()
        while True:
            self.publish_heartbeat()
            time.sleep(interval_seconds)

    def run_example_cycle(self) -> None:
        """Run a simple example cycle of registration, telemetry, and problem reporting."""
        self.register()
        self.publish_telemetry(success=True, latency_ms=240, extra={"session_count": 7})
        self.publish_telemetry(success=False, latency_ms=1200, extra={"session_count": 1})
        self.report_problem(
            "Conversation model timed out on a large Canvas prompt",
            severity="warning",
            details={"conversation_id": "tutor-abc123", "retryable": True},
        )


if __name__ == "__main__":
    adapter = CanvasTutorAdapter()
    if os.environ.get("CANVAS_TUTOR_EXAMPLE_CYCLE", "").lower() in {"1", "true", "yes"}:
        adapter.run_example_cycle()
    else:
        interval = int(os.environ.get("CANVAS_TUTOR_HEARTBEAT_INTERVAL_SECONDS", "60"))
        adapter.run_heartbeat_loop(interval_seconds=interval)
