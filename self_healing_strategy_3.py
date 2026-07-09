"""
Self-Healing Strategy #3: Config Reload on Error

When an operation fails, reload fresh configuration and retry once.
Many failures are caused by stale config (wrong URLs, timeouts, thresholds),
so a config refresh + single retry fixes them without a full restart.
"""

import asyncio
from typing import Callable, Any, Optional


class ConfigReloadHealer:
    """Reloads config and retries failed operations."""

    def __init__(self, agent):
        """
        Args:
            agent: Agent instance (must have .config, .config_manager, .logger, .health_monitor)
        """
        self.agent = agent
        self.logger = agent.logger
        self.reload_count = 0

    async def execute_with_healing(
        self,
        operation: Callable,
        operation_name: str,
        *args,
        **kwargs,
    ) -> tuple[bool, Any]:
        """
        Execute an operation. On failure: reload config, retry once.

        Args:
            operation: The function to execute (sync or async)
            operation_name: Name for logging
            *args, **kwargs: Passed to the operation

        Returns:
            (success: bool, result: Any)
        """
        # First attempt
        try:
            result = await self._call(operation, *args, **kwargs)
            self.agent.health_monitor.record_task_completion()
            return True, result

        except Exception as first_error:
            self.logger.log_event(
                "operation_failed_attempting_config_reload",
                agent_id=self.agent.agent_id,
                operation=operation_name,
                error=str(first_error),
            )

            # Reload config
            reloaded = self._reload_config()
            if not reloaded:
                self.agent.health_monitor.record_error()
                return False, first_error

            # Retry once with fresh config
            try:
                result = await self._call(operation, *args, **kwargs)
                self.logger.log_event(
                    "operation_succeeded_after_config_reload",
                    agent_id=self.agent.agent_id,
                    operation=operation_name,
                )
                self.agent.health_monitor.record_task_completion()
                return True, result

            except Exception as second_error:
                self.logger.log_event(
                    "operation_failed_after_config_reload",
                    agent_id=self.agent.agent_id,
                    operation=operation_name,
                    error=str(second_error),
                )
                self.agent.health_monitor.record_error()
                return False, second_error

    async def _call(self, operation: Callable, *args, **kwargs) -> Any:
        """Call operation, supporting both sync and async functions."""
        if asyncio.iscoroutinefunction(operation):
            return await operation(*args, **kwargs)
        return operation(*args, **kwargs)

    def _reload_config(self) -> bool:
        """Force reload configuration from source."""
        try:
            old_config = self.agent.config
            new_config = self.agent.config_manager.reload_config(self.agent.agent_id)

            # Validate before applying
            is_valid, errors = new_config.validate()
            if not is_valid:
                self.logger.log_event(
                    "reloaded_config_invalid",
                    agent_id=self.agent.agent_id,
                    errors=errors,
                )
                return False

            self.agent.config = new_config
            self.reload_count += 1

            self.logger.log_event(
                "config_reloaded_successfully",
                agent_id=self.agent.agent_id,
                reload_count=self.reload_count,
            )
            return True

        except Exception as e:
            self.logger.log_error(e, {
                "action": "config_reload",
                "agent_id": self.agent.agent_id,
            })
            return False