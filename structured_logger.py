"""
StructuredLogger - JSON-based structured logging for agents.
Outputs logs in JSON format for easy parsing and aggregation.
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class StructuredLogger:
    """
    A structured logger that outputs logs in JSON format.
    Each log entry is a complete JSON object with timestamp and context.
    """
    
    def __init__(self, name: str = "agent_logger", level: str = "INFO", agent_id: str = None):
        """
        Initialize the StructuredLogger.
        
        Args:
            name: Logger name
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            agent_id: Agent identifier for context
        """
        self.name = name
        self.agent_id = agent_id
        
        # Convert level string to integer
        level_int = getattr(logging, level.upper(), logging.INFO)
        
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level_int)
        
        # Configure handler to output to stdout
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('%(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format"""
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    
    def _redact_sensitive(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Redact sensitive fields from log data.
        Sensitive fields: password, token, secret, api_key, authorization
        """
        sensitive_fields = {'password', 'token', 'secret', 'api_key', 'authorization'}
        redacted = {}
        
        for key, value in data.items():
            if any(field in key.lower() for field in sensitive_fields):
                redacted[key] = '[REDACTED]'
            else:
                redacted[key] = value
        
        return redacted
    
    def log_event(self, event_name: str, **kwargs) -> None:
        """
        Log an event with custom fields.
        
        Args:
            event_name: Name of the event
            **kwargs: Additional fields to include in the log
        
        Example:
            logger.log_event("agent_started", agent_id="tutor_1", version="1.2.3")
        """
        log_entry = {
            'timestamp': self._get_timestamp(),
            'event': event_name,
        }
        
        if self.agent_id:
            log_entry['agent_id'] = self.agent_id
        
        log_entry.update(self._redact_sensitive(kwargs))
        
        self.logger.info(json.dumps(log_entry))
    
    def log_metric(self, metric_name: str, value: Any, **kwargs) -> None:
        """
        Log a metric with value.
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            **kwargs: Additional context fields
        
        Example:
            logger.log_metric("queue_depth", 42, agent_id="tutor_1")
        """
        log_entry = {
            'timestamp': self._get_timestamp(),
            'metric': metric_name,
            'value': value,
        }
        
        if self.agent_id:
            log_entry['agent_id'] = self.agent_id
        
        log_entry.update(self._redact_sensitive(kwargs))
        
        self.logger.info(json.dumps(log_entry))
    
    def log_error(self, exception: Exception, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Log an error with exception details and context.
        
        Args:
            exception: The exception that occurred
            context: Additional context information
        
        Example:
            try:
                something()
            except Exception as e:
                logger.log_error(e, {"agent_id": "tutor_1", "action": "sync"})
        """
        import traceback
        
        context = context or {}
        
        log_entry = {
            'timestamp': self._get_timestamp(),
            'event': 'error',
            'error_type': exception.__class__.__name__,
            'error_message': str(exception),
            'stack_trace': traceback.format_exc(),
        }
        
        if self.agent_id:
            log_entry['agent_id'] = self.agent_id
        
        log_entry.update(self._redact_sensitive(context))
        
        self.logger.error(json.dumps(log_entry))
    
    def log_debug(self, message: str, **kwargs) -> None:
        """
        Log a debug message with additional fields.
        
        Args:
            message: Debug message
            **kwargs: Additional fields
        """
        log_entry = {
            'timestamp': self._get_timestamp(),
            'level': 'DEBUG',
            'message': message,
        }
        
        if self.agent_id:
            log_entry['agent_id'] = self.agent_id
        
        log_entry.update(self._redact_sensitive(kwargs))
        
        self.logger.debug(json.dumps(log_entry))


# Global logger instance for convenient access
_default_logger = StructuredLogger()

def get_logger(name: str = "agent_logger", agent_id: str = None) -> StructuredLogger:
    """Get a logger instance by name"""
    return StructuredLogger(name, agent_id=agent_id)
