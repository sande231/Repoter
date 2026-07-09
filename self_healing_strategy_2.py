"""
Self-Healing Strategy #2: Auto-Reconnect on Connection Failure

If connection to the ingestion server fails, retry with exponential
backoff + jitter until it recovers, then flush any queued telemetry.
"""

import asyncio
import random
from typing import Optional, Callable, Any


class AutoReconnectHealer:
    """Automatically reconnects to the ingestion server with backoff."""

    def __init__(
        self,
        agent,
        max_attempts: int = 5,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
    ):
        """
        Args:
            agent: Agent instance (must have .sdk, .logger, .health_monitor)
            max_attempts: Max reconnection attempts per healing cycle
            base_delay_seconds: Initial backoff delay
            max_delay_seconds: Cap on backoff delay
        """
        self.agent = agent
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.logger = agent.logger
        self.consecutive_failures = 0

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter: base * 2^attempt + random(0-1s)."""
        delay = min(
            self.base_delay_seconds * (2 ** attempt),
            self.max_delay_seconds,
        )
        return delay + random.uniform(0, 1)

    async def check_connection(self) -> bool:
        """Test if the ingestion server is reachable via a lightweight probe."""
        try:
            # Use the SDK's session/requests to hit the /health endpoint
            import requests
            url = f"{self.agent.sdk.ingestion_url.rstrip('/')}/health"
            resp = requests.get(url, timeout=3)
            return resp.ok
        except Exception:
            return False

    async def heal(self) -> bool:
        """
        Attempt to restore connectivity with exponential backoff.

        Returns:
            True if connection restored, False if all attempts failed.
        """
        self.logger.log_event(
            "auto_reconnect_started",
            agent_id=self.agent.agent_id,
            max_attempts=self.max_attempts,
        )

        for attempt in range(self.max_attempts):
            connected = await self.check_connection()

            if connected:
                self.logger.log_event(
                    "auto_reconnect_succeeded",
                    agent_id=self.agent.agent_id,
                    attempt=attempt + 1,
                )
                self.consecutive_failures = 0

                # Flush queued telemetry now that we're back online
                await self._flush_pending_queue()
                return True

            delay = self._backoff_delay(attempt)
            self.logger.log_event(
                "auto_reconnect_attempt_failed",
                agent_id=self.agent.agent_id,
                attempt=attempt + 1,
                next_retry_in_seconds=round(delay, 2),
            )
            await asyncio.sleep(delay)

        self.consecutive_failures += 1
        self.logger.log_event(
            "auto_reconnect_exhausted",
            agent_id=self.agent.agent_id,
            consecutive_failures=self.consecutive_failures,
        )
        self.agent.health_monitor.record_error()
        return False

    async def _flush_pending_queue(self) -> None:
        """Send any telemetry that was queued locally while offline."""
        local_queue = getattr(self.agent.sdk, "local_queue", None)
        if not local_queue:
            return

        try:
            pending = local_queue.get_pending_count()
        except Exception:
            return

        if pending == 0:
            return

        self.logger.log_event(
            "flushing_offline_queue",
            agent_id=self.agent.agent_id,
            pending_items=pending,
        )

        flushed = 0
        while True:
            try:
                item = local_queue.dequeue()
                if item is None:
                    break
                response = self.agent.sdk.send_metrics(item)
                if response is None:
                    # Server went down again mid-flush; stop
                    self.logger.log_event("queue_flush_interrupted", flushed=flushed)
                    return
                flushed += 1
            except Exception as e:
                self.logger.log_error(e, {"action": "queue_flush"})
                break

        self.logger.log_event(
            "offline_queue_flushed",
            agent_id=self.agent.agent_id,
            items_flushed=flushed,
        )
        self.agent.health_monitor.record_task_completion()