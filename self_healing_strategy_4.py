"""
Self-Healing Strategy #4: Queue Self-Healing

Detects when the local telemetry queue has a large backlog of stuck items
and drains it in controlled batches. Stops safely if the server goes down
mid-drain, and drops items that repeatedly fail (poison messages).
"""

import asyncio
from typing import Optional


class QueueHealer:
    """Detects and drains queue backlogs safely."""

    def __init__(
        self,
        agent,
        backlog_threshold: int = 100,
        batch_size: int = 50,
        batch_pause_seconds: float = 0.5,
        max_items_per_heal: int = 1000,
    ):
        """
        Args:
            agent: Agent instance (must have .sdk with .local_queue, .logger, .health_monitor)
            backlog_threshold: Queue size that triggers healing
            batch_size: Items to send between pauses (avoids hammering server)
            batch_pause_seconds: Pause between batches
            max_items_per_heal: Safety cap per healing cycle
        """
        self.agent = agent
        self.backlog_threshold = backlog_threshold
        self.batch_size = batch_size
        self.batch_pause_seconds = batch_pause_seconds
        self.max_items_per_heal = max_items_per_heal
        self.logger = agent.logger

    def _get_queue(self):
        return getattr(self.agent.sdk, "local_queue", None)

    def _pending_count(self) -> Optional[int]:
        queue = self._get_queue()
        if not queue:
            return None
        try:
            return queue.get_pending_count()
        except Exception:
            return None

    async def check_and_heal(self) -> dict:
        """
        Check queue depth; drain if over threshold.

        Returns:
            dict with: healed (bool), flushed (int), failed (int), remaining (int)
        """
        result = {"healed": False, "flushed": 0, "failed": 0, "remaining": 0}

        pending = self._pending_count()
        if pending is None:
            self.logger.log_event("queue_healer_no_queue", agent_id=self.agent.agent_id)
            return result

        result["remaining"] = pending

        if pending < self.backlog_threshold:
            # Queue is healthy, nothing to do
            return result

        self.logger.log_event(
            "queue_backlog_detected",
            agent_id=self.agent.agent_id,
            pending_items=pending,
            threshold=self.backlog_threshold,
        )

        queue = self._get_queue()
        flushed = 0
        failed = 0

        while flushed + failed < self.max_items_per_heal:
            # Batch pause to avoid hammering the server
            if flushed > 0 and flushed % self.batch_size == 0:
                self.logger.log_event(
                    "queue_heal_batch_complete",
                    agent_id=self.agent.agent_id,
                    flushed_so_far=flushed,
                )
                await asyncio.sleep(self.batch_pause_seconds)

            try:
                item = queue.dequeue()
                if item is None:
                    break  # Queue empty — done!

                response = self.agent.sdk.send_metrics(item)
                if response is None:
                    # Server unreachable mid-drain — stop, items stay queued
                    self.logger.log_event(
                        "queue_heal_interrupted_server_down",
                        agent_id=self.agent.agent_id,
                        flushed=flushed,
                    )
                    result.update(
                        healed=False, flushed=flushed, failed=failed,
                        remaining=self._pending_count() or 0,
                    )
                    return result

                flushed += 1

            except Exception as e:
                failed += 1
                self.logger.log_error(e, {
                    "action": "queue_heal_item",
                    "agent_id": self.agent.agent_id,
                })
                if failed >= 5:
                    # Too many poison messages — stop this cycle
                    self.logger.log_event(
                        "queue_heal_too_many_failures",
                        agent_id=self.agent.agent_id,
                        failed=failed,
                    )
                    break

        remaining = self._pending_count() or 0
        result.update(healed=True, flushed=flushed, failed=failed, remaining=remaining)

        self.logger.log_event(
            "queue_heal_completed",
            agent_id=self.agent.agent_id,
            items_flushed=flushed,
            items_failed=failed,
            items_remaining=remaining,
        )
        self.agent.health_monitor.record_task_completion()
        return result