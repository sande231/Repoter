"""
Logging Decorator - Automatic function execution logging.
The @log_execution decorator logs function calls, arguments, return values, and execution time.
"""

import functools
import time
import traceback
import logging
import json
from typing import Any, Callable
from datetime import datetime


def log_execution(func: Callable) -> Callable:
    """
    Decorator that logs function execution details.
    
    Logs:
    - Function name and module
    - Input arguments (args and kwargs)
    - Execution duration in milliseconds
    - Return value (on success)
    - Exception details (on failure)
    """
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        logger = logging.getLogger(func.__module__)
        func_name = func.__name__
        module_name = func.__module__
        
        args_list = list(args)
        if args_list and hasattr(args_list[0], '__dict__'):
            args_list = args_list[1:]
        
        log_entry = {
            'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'event': 'function_call',
            'function': func_name,
            'module': module_name,
            'args': str(args_list) if args_list else None,
            'kwargs': kwargs if kwargs else None,
        }
        
        logger.info(f"[ENTRY] {func_name} called with args={args_list}, kwargs={kwargs}")
        
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            
            log_entry['status'] = 'success'
            log_entry['duration_ms'] = round(duration_ms, 3)
            log_entry['return_value'] = str(result) if result is not None else None
            
            logger.info(f"[EXIT] {func_name} completed in {duration_ms:.3f}ms, returned: {result}")
            logger.info(json.dumps(log_entry))
            
            return result
            
        except Exception as e:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            
            log_entry['status'] = 'error'
            log_entry['duration_ms'] = round(duration_ms, 3)
            log_entry['error_type'] = e.__class__.__name__
            log_entry['error_message'] = str(e)
            log_entry['stack_trace'] = traceback.format_exc()
            
            logger.error(f"[ERROR] {func_name} failed after {duration_ms:.3f}ms: {e}")
            logger.error(json.dumps(log_entry))
            
            raise
    
    return wrapper


def log_execution_async(func: Callable) -> Callable:
    """
    Async version of log_execution decorator.
    Use this for async functions.
    """
    
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        logger = logging.getLogger(func.__module__)
        func_name = func.__name__
        module_name = func.__module__
        
        args_list = list(args)
        if args_list and hasattr(args_list[0], '__dict__'):
            args_list = args_list[1:]
        
        log_entry = {
            'timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'event': 'async_function_call',
            'function': func_name,
            'module': module_name,
            'args': str(args_list) if args_list else None,
            'kwargs': kwargs if kwargs else None,
        }
        
        logger.info(f"[ASYNC ENTRY] {func_name} called")
        
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            
            log_entry['status'] = 'success'
            log_entry['duration_ms'] = round(duration_ms, 3)
            log_entry['return_value'] = str(result) if result is not None else None
            
            logger.info(f"[ASYNC EXIT] {func_name} completed in {duration_ms:.3f}ms")
            logger.info(json.dumps(log_entry))
            
            return result
            
        except Exception as e:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000
            
            log_entry['status'] = 'error'
            log_entry['duration_ms'] = round(duration_ms, 3)
            log_entry['error_type'] = e.__class__.__name__
            log_entry['error_message'] = str(e)
            log_entry['stack_trace'] = traceback.format_exc()
            
            logger.error(f"[ASYNC ERROR] {func_name} failed: {e}")
            logger.error(json.dumps(log_entry))
            
            raise
    
    return wrapper