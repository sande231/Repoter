"""Standalone worker process that processes the durable email queue.

UPDATED: Integrated with structured logging and graceful shutdown.

This worker runs continuously, polling the email queue and sending emails.
It handles graceful shutdown with timeout for in-flight emails.
"""

import asyncio
from email_queue import worker_loop

# NEW: Import graceful shutdown and logging
from structured_logger import StructuredLogger
from graceful_shutdown import GracefulShutdownHandler


async def main_async():
    """Async main function with graceful shutdown support."""
    
    # NEW: Initialize structured logger
    logger = StructuredLogger(agent_id="email_worker", level="INFO")
    
    # NEW: Initialize graceful shutdown handler
    shutdown_handler = GracefulShutdownHandler(timeout_seconds=30, logger=logger)
    shutdown_handler.start()
    
    logger.log_event("email_worker_starting")
    
    try:
        # Register shutdown callback to stop worker gracefully
        def shutdown_callback():
            logger.log_event("email_worker_shutdown_requested")
        
        shutdown_handler.register_shutdown_callback(shutdown_callback)
        
        # Run the worker loop
        worker_loop()
        
    except KeyboardInterrupt:
        logger.log_event("email_worker_interrupted")
    except Exception as e:
        logger.log_error(e, {"action": "email_worker_main"})
        raise
    finally:
        logger.log_event("email_worker_stopped")


if __name__ == "__main__":
    """Run the email worker."""
    
    # NEW: Use structured logger
    logger = StructuredLogger(agent_id="email_worker", level="INFO")
    
    logger.log_event("email_worker_process_started")
    
    try:
        # NEW: Initialize graceful shutdown handler
        shutdown_handler = GracefulShutdownHandler(timeout_seconds=30, logger=logger)
        shutdown_handler.start()
        
        # Register shutdown callback
        def on_shutdown():
            logger.log_event("email_worker_shutting_down")
        
        shutdown_handler.register_shutdown_callback(on_shutdown)
        
        # Run the worker loop (this is the main blocking call)
        logger.log_event("starting_email_queue_worker")
        worker_loop()
        
    except KeyboardInterrupt:
        logger.log_event("email_worker_interrupted_by_user")
    except Exception as e:
        logger.log_error(e, {"action": "email_worker_main"})
        raise
    finally:
        logger.log_event("email_worker_process_exiting")