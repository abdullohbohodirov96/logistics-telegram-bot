"""
Simple in-memory cache with TTL and max size for reducing DB/API calls.
"""
import time
import logging

logger = logging.getLogger(__name__)

_cache = {}
MAX_CACHE_SIZE = 20  # Never store more than 20 keys

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
    """Store a value in cache with current timestamp. Evicts oldest if full."""
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        # Evict oldest entry
        oldest_key = min(_cache, key=lambda k: _cache[k][1])
        del _cache[oldest_key]
    _cache[key] = (value, time.time())

def cache_clear(key: str = None):
    """Clear a specific key or entire cache."""
    global _cache
    if key:
        _cache.pop(key, None)
    else:
        _cache = {}
