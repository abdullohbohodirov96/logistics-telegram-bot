"""
Simple in-memory cache with TTL for reducing DB/API calls.
"""
import time
import logging

logger = logging.getLogger(__name__)

_cache = {}

def cache_get(key: str, ttl_seconds: int = 60):
    """Get a cached value if it exists and hasn't expired."""
    entry = _cache.get(key)
    if entry is None:
        return None
    value, ts = entry
    if time.time() - ts > ttl_seconds:
        del _cache[key]
        return None
    return value

def cache_set(key: str, value):
    """Store a value in cache with current timestamp."""
    _cache[key] = (value, time.time())

def cache_clear(key: str = None):
    """Clear a specific key or entire cache."""
    global _cache
    if key:
        _cache.pop(key, None)
    else:
        _cache = {}
