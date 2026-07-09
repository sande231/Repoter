"""
Agent Configuration - Dynamic configuration for agents.
Allows agents to fetch and manage their configuration centrally.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


@dataclass
class AgentConfig:
    """
    Configuration dataclass for agents.
    Contains all settings agents need to operate.
    """
    
    # Telemetry settings
    telemetry_batch_size: int = 50
    telemetry_batch_timeout_seconds: int = 5
    
    # Retry settings
    retry_max_attempts: int = 5
    retry_initial_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 60.0
    
    # Health check settings
    health_check_interval_seconds: int = 30
    alert_error_threshold_percent: float = 20.0
    alert_timeout_seconds: int = 300
    
    # Logging settings
    log_level: str = "INFO"
    
    # Optional features
    features_enabled: Dict[str, bool] = field(default_factory=lambda: {
        "auto_remediation": True,
        "detailed_logging": False,
        "performance_monitoring": True
    })
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert configuration to JSON string"""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentConfig':
        """Create AgentConfig from dictionary"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'AgentConfig':
        """Create AgentConfig from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    def validate(self) -> tuple:
        """
        Validate configuration values.
        Returns (is_valid, list_of_errors)
        """
        errors = []
        
        if self.telemetry_batch_size < 1:
            errors.append("telemetry_batch_size must be >= 1")
        
        if self.telemetry_batch_timeout_seconds < 1:
            errors.append("telemetry_batch_timeout_seconds must be >= 1")
        
        if self.retry_max_attempts < 1:
            errors.append("retry_max_attempts must be >= 1")
        
        if self.retry_initial_delay_seconds < 0:
            errors.append("retry_initial_delay_seconds must be >= 0")
        
        if self.retry_max_delay_seconds < self.retry_initial_delay_seconds:
            errors.append("retry_max_delay_seconds must be >= retry_initial_delay_seconds")
        
        if self.health_check_interval_seconds < 1:
            errors.append("health_check_interval_seconds must be >= 1")
        
        if not (0 <= self.alert_error_threshold_percent <= 100):
            errors.append("alert_error_threshold_percent must be between 0 and 100")
        
        if self.log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            errors.append(f"log_level must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL")
        
        return len(errors) == 0, errors


class ConfigManager:
    """
    Configuration manager for agents.
    Loads, caches, and manages configurations.
    """
    
    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize ConfigManager.
        
        Args:
            cache_ttl_seconds: Time to live for cached configs (default: 5 minutes)
        """
        self.cache_ttl_seconds = cache_ttl_seconds
        self._config_cache: Dict[str, tuple] = {}
        self._default_config = AgentConfig()
    
    def get_config(self, agent_id: str) -> AgentConfig:
        """
        Get configuration for an agent.
        Checks cache first, then loads from file/env.
        
        Args:
            agent_id: The agent identifier
            
        Returns:
            AgentConfig instance
        """
        if agent_id in self._config_cache:
            config, timestamp = self._config_cache[agent_id]
            if datetime.now() - timestamp < timedelta(seconds=self.cache_ttl_seconds):
                return config
        
        config = self._load_config_from_source(agent_id)
        self._config_cache[agent_id] = (config, datetime.now())
        
        return config
    
    def set_config(self, agent_id: str, config: AgentConfig) -> bool:
        """
        Set configuration for an agent.
        
        Args:
            agent_id: The agent identifier
            config: The AgentConfig to set
            
        Returns:
            True if successful, False otherwise
        """
        is_valid, errors = config.validate()
        if not is_valid:
            print(f"Configuration validation failed: {errors}")
            return False
        
        self._config_cache[agent_id] = (config, datetime.now())
        self._save_config_to_file(agent_id, config)
        
        return True
    
    def reload_config(self, agent_id: str) -> AgentConfig:
        """
        Force reload configuration from source (bypass cache).
        
        Args:
            agent_id: The agent identifier
            
        Returns:
            Reloaded AgentConfig
        """
        if agent_id in self._config_cache:
            del self._config_cache[agent_id]
        
        return self.get_config(agent_id)
    
    def _load_config_from_source(self, agent_id: str) -> AgentConfig:
        """
        Load configuration from environment or file.
        
        Args:
            agent_id: The agent identifier
            
        Returns:
            AgentConfig instance (or default if not found)
        """
        env_var = f"AGENT_CONFIG_{agent_id.upper()}"
        if env_var in os.environ:
            try:
                json_str = os.environ[env_var]
                return AgentConfig.from_json(json_str)
            except Exception as e:
                print(f"Error parsing config from {env_var}: {e}")
        
        config_file = f"config_{agent_id}.json"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    data = json.load(f)
                    return AgentConfig.from_dict(data)
            except Exception as e:
                print(f"Error loading config from {config_file}: {e}")
        
        return self._default_config
    
    def _save_config_to_file(self, agent_id: str, config: AgentConfig) -> bool:
        """
        Save configuration to file.
        
        Args:
            agent_id: The agent identifier
            config: The AgentConfig to save
            
        Returns:
            True if successful, False otherwise
        """
        try:
            config_file = f"config_{agent_id}.json"
            with open(config_file, 'w') as f:
                json.dump(config.to_dict(), f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving config to {config_file}: {e}")
            return False
    
    def clear_cache(self):
        """Clear all cached configurations"""
        self._config_cache.clear()


_default_manager = ConfigManager()

def get_manager() -> ConfigManager:
    """Get the default ConfigManager instance"""
    return _default_manager
