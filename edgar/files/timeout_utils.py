import signal
import platform
import time
import logging
from functools import wraps


class TimeoutException(Exception):
    """Timeout exception class, thrown when processing time exceeds the threshold"""

    pass


def timeout_handler(signum, frame):
    """Triggered when timeout occurs, throws timeout exception"""
    raise TimeoutException("Processing timeout")


def monitor_performance(func):
    """Decorator to monitor function performance and log slow operations"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            if elapsed > 2.0:  # Log operations taking more than 2 seconds
                logging.warning(f"{func.__name__} took {elapsed:.2f} seconds")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logging.error(
                f"{func.__name__} failed after {elapsed:.2f} seconds: {e}"
            )
            raise

    return wrapper


class TimeoutManager:
    """Context manager for handling timeouts"""

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self.supports_alarm = platform.system() != "Windows"

    def __enter__(self):
        if self.supports_alarm:
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(self.timeout_seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.supports_alarm:
            signal.alarm(0)
