"""
Self-Healing Strategy #1: Auto-Restart on Degraded Status

If an agent is degraded for too long, automatically restart it.
"""

import asyncio
import time
from typing import Optional
from datetime import datetime, timedelta


class AutoRestartHealer:
    """Automatically restarts agent if degraded too long."""
    
    def __init__(self, agent, degraded_threshold_seconds: int = 300):
        """
        Initialize AutoRestartHealer.
        
        Args:
            agent: Agent instance to monitor
            degraded_threshold_seconds: How long agent can be degraded before restart (default: 5 min)
        """
        self.agent = agent
        self.degraded_threshold_seconds = degraded_threshold_seconds
        self.degraded_start_time: Optional[datetime] = None
        self.logger = agent.logger
    
    async def check_and_heal(self) -> bool:
        """
        Check health and restart if needed.
        
        Returns:
            True if healing occurred, False otherwise
        """
        health = self.agent.get_health_check()
        
        # Track degraded status
        if health.status == "degraded":
            if self.degraded_start_time is None:
                self.degraded_start_time = datetime.now()
                self.logger.log_event(
                    "agent_degraded_start",
                    agent_id=self.agent.agent_id,
                    error_rate=health.error_rate_percent
                )
                return False
            
            # Check how long degraded
            elapsed = (datetime.now() - self.degraded_start_time).total_seconds()
            
            if elapsed >= self.degraded_threshold_seconds:
                self.logger.log_event(
                    "auto_restart_triggered",
                    agent_id=self.agent.agent_id,
                    reason="degraded_too_long",
                    degraded_for_seconds=elapsed,
                    error_rate=health.error_rate_percent
                )
                
                # Perform restart
                await self._perform_restart()
                return True
            else:
                remaining = self.degraded_threshold_seconds - elapsed
                self.logger.log_event(
                    "agent_still_degraded",
                    agent_id=self.agent.agent_id,
                    seconds_until_restart=remaining,
                    error_rate=health.error_rate_percent
                )
                return False
        
        else:
            # Agent is healthy again
            if self.degraded_start_time is not None:
                self.logger.log_event(
                    "agent_recovered",
                    agent_id=self.agent.agent_id,
                    status=health.status
                )
                self.degraded_start_time = None
            
            return False
    
    async def _perform_restart(self) -> None:
        """Perform graceful restart of the agent."""
        try:
            self.logger.log_event(
                "auto_restart_shutdown_start",
                agent_id=self.agent.agent_id
            )
            
            # Shutdown
            await self.agent.on_shutdown()
            
            # Wait before restart
            self.logger.log_event(
                "auto_restart_waiting",
                agent_id=self.agent.agent_id,
                wait_seconds=10
            )
            await asyncio.sleep(10)
            
            # Startup
            self.logger.log_event(
                "auto_restart_startup_start",
                agent_id=self.agent.agent_id
            )
            await self.agent.on_startup()
            
            # Reset degraded tracking
            self.degraded_start_time = None
            
            self.logger.log_event(
                "auto_restart_completed_successfully",
                agent_id=self.agent.agent_id
            )
        
        except Exception as e:
            self.logger.log_error(e, {
                "action": "auto_restart",
                "agent_id": self.agent.agent_id
            })


async def run_healer_loop(agent, check_interval_seconds: int = 60):
    """
    Run the auto-restart healer in a background loop.
    
    Args:
        agent: Agent instance to monitor
        check_interval_seconds: How often to check health (default: every 60 seconds)
    """
    healer = AutoRestartHealer(agent)
    agent.logger.log_event("auto_restart_healer_started", check_interval=check_interval_seconds)
    
    try:
        while not agent.is_shutting_down():
            await healer.check_and_heal()
            await asyncio.sleep(check_interval_seconds)
    
    except Exception as e:
        agent.logger.log_error(e, {"action": "healer_loop"})
    
    finally:
        agent.logger.log_event("auto_restart_healer_stopped")