"""
S3 utility functions for edgartools
"""
import os
import logging
from functools import lru_cache
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def is_s3_configured() -> bool:
    """
    Check if S3 is properly configured by verifying required environment variables.
    
    Returns:
        bool: True if S3 is configured, False otherwise
    """
    required_vars = ["S3_BUCKET_NAME", "S3_AK", "S3_SK"]
    
    for var in required_vars:
        if not os.environ.get(var):
            logger.debug(f"S3 configuration incomplete: {var} not set")
            return False
    
    logger.debug("S3 configuration detected")
    return True


def should_use_s3_cache(use_s3_cache: bool | None = None) -> bool:
    """
    Determine whether to use S3 cache based on configuration and user preference.
    
    Args:
        use_s3_cache: User's explicit preference. If None, auto-detect based on configuration.
        
    Returns:
        bool: True if S3 cache should be used, False otherwise
    """
    # If user explicitly set the preference, respect it
    if use_s3_cache is not None:
        if use_s3_cache and not is_s3_configured():
            logger.warning("S3 cache requested but not configured, falling back to no cache")
            return False
        return use_s3_cache
    
    # Auto-detect based on S3 configuration
    return is_s3_configured()


def get_s3_cache_key(url: str) -> str:
    """
    Generate a consistent S3 cache key from a URL.
    
    Args:
        url: The URL to generate a cache key for
        
    Returns:
        str: A valid S3 key for caching the URL content
    """
    # Replace problematic characters for S3 key
    safe_url = url.replace('http://', '_').replace('https://', '_').replace('?', '_').replace('&', '_')
    return f"edgar_cache/{safe_url}"
