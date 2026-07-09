"""
Config Service - Flask-based configuration service for agents.
Provides endpoints for agents to fetch and update their configuration.
"""

import json
from flask import Flask, jsonify, request
from typing import Dict, Optional
from datetime import datetime, timedelta
from agent_config import AgentConfig, ConfigManager
from structured_logger import StructuredLogger


class ConfigServiceAPI:
    """
    Flask-based configuration service API.
    Serves agent configurations via REST endpoints.
    """
    
    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """
        Initialize ConfigServiceAPI.
        
        Args:
            config_manager: ConfigManager instance (creates default if None)
        """
        self.app = Flask(__name__)
        self.config_manager = config_manager or ConfigManager()
        self.logger = StructuredLogger(agent_id="config_service")
        
        # Request cache
        self._request_cache: Dict[str, tuple[dict, datetime]] = {}
        self.cache_ttl = 300  # 5 minutes
        
        # Setup routes
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint"""
            self.logger.log_event("health_check_request")
            return jsonify({"status": "healthy"}), 200
        
        @self.app.route('/config/<agent_id>', methods=['GET'])
        def get_config(agent_id):
            """
            GET /config/<agent_id> - Get agent configuration
            """
            self.logger.log_event("config_request", agent_id=agent_id, method="GET")
            
            try:
                config = self.config_manager.get_config(agent_id)
                response = {
                    "status": "success",
                    "agent_id": agent_id,
                    "config": config.to_dict(),
                    "timestamp": datetime.utcnow().isoformat()
                }
                return jsonify(response), 200
            
            except Exception as e:
                self.logger.log_error(e, {"agent_id": agent_id, "endpoint": "/config/<agent_id>"})
                return jsonify({
                    "status": "error",
                    "message": str(e),
                    "agent_id": agent_id
                }), 500
        
        @self.app.route('/config/<agent_id>', methods=['POST'])
        def update_config(agent_id):
            """
            POST /config/<agent_id> - Update agent configuration
            """
            self.logger.log_event("config_update_request", agent_id=agent_id, method="POST")
            
            try:
                # Get JSON data
                data = request.get_json()
                if not data:
                    return jsonify({
                        "status": "error",
                        "message": "Request body must be JSON"
                    }), 400
                
                # Create new config
                new_config = AgentConfig.from_dict(data)
                
                # Validate
                is_valid, errors = new_config.validate()
                if not is_valid:
                    self.logger.log_event("config_validation_failed", agent_id=agent_id, errors=errors)
                    return jsonify({
                        "status": "error",
                        "message": "Configuration validation failed",
                        "errors": errors
                    }), 400
                
                # Save
                success = self.config_manager.set_config(agent_id, new_config)
                if not success:
                    return jsonify({
                        "status": "error",
                        "message": "Failed to save configuration"
                    }), 500
                
                self.logger.log_event("config_updated", agent_id=agent_id)
                
                return jsonify({
                    "status": "success",
                    "agent_id": agent_id,
                    "config": new_config.to_dict(),
                    "timestamp": datetime.utcnow().isoformat()
                }), 200
            
            except json.JSONDecodeError:
                return jsonify({
                    "status": "error",
                    "message": "Invalid JSON in request body"
                }), 400
            
            except Exception as e:
                self.logger.log_error(e, {"agent_id": agent_id, "endpoint": "/config/<agent_id> POST"})
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500
        
        @self.app.route('/config/<agent_id>/validate', methods=['GET'])
        def validate_config(agent_id):
            """
            GET /config/<agent_id>/validate - Validate agent configuration
            """
            self.logger.log_event("config_validate_request", agent_id=agent_id)
            
            try:
                config = self.config_manager.get_config(agent_id)
                is_valid, errors = config.validate()
                
                return jsonify({
                    "status": "success",
                    "agent_id": agent_id,
                    "is_valid": is_valid,
                    "errors": errors,
                    "config": config.to_dict()
                }), 200
            
            except Exception as e:
                self.logger.log_error(e, {"agent_id": agent_id, "endpoint": "/config/<agent_id>/validate"})
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500
        
        @self.app.route('/metrics', methods=['GET'])
        def metrics():
            """
            GET /metrics - Get service metrics
            """
            return jsonify({
                "status": "success",
                "cache_size": len(self.config_manager._config_cache),
                "timestamp": datetime.utcnow().isoformat()
            }), 200
        
        @self.app.errorhandler(404)
        def not_found(e):
            """Handle 404 errors"""
            return jsonify({
                "status": "error",
                "message": "Endpoint not found"
            }), 404
    
    def run(self, host: str = "127.0.0.1", port: int = 5001, debug: bool = False):
        """
        Run the Flask app.
        
        Args:
            host: Host to bind to
            port: Port to bind to
            debug: Whether to run in debug mode
        """
        self.logger.log_event("config_service_starting", host=host, port=port)
        self.app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    # Create service
    service = ConfigServiceAPI()
    
    # Create a sample config and save it
    sample_config = AgentConfig(
        telemetry_batch_size=100,
        health_check_interval_seconds=60
    )
    service.config_manager.set_config("example_agent", sample_config)
    
    # Run the service
    print("Starting Config Service on http://127.0.0.1:5001")
    service.run(debug=True)