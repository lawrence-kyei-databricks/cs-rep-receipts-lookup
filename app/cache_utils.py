"""
Receipt caching utilities for performance optimization.

Provides in-memory LRU cache for frequently accessed receipts to reduce
database load and improve response times.
"""

import time
import logging
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ReceiptCache:
    """
    LRU cache for receipt lookups with TTL expiration.

    Caches complete receipt objects (including line items) to avoid
    redundant database queries when CS reps repeatedly access the same receipts.

    Cache Strategy:
    - LRU eviction when max_size is reached
    - TTL-based expiration (default 15 minutes)
    - Separate cache for receipt lookups vs customer receipt lists

    Performance Impact:
    - Cache hit: ~1-2ms (in-memory lookup)
    - Cache miss: ~8-15ms (database query via pooled connection)
    - Expected hit rate: 30-50% for receipt lookups (CS reps reviewing same receipts)
    """

    def __init__(self, max_size: int = 500, ttl_seconds: int = 900):
        """
        Initialize receipt cache.

        Args:
            max_size: Maximum number of cached receipts (default: 500)
            ttl_seconds: Time-to-live for cache entries in seconds (default: 900 = 15 minutes)
        """
        self._cache: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds

        # Cache statistics for monitoring
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0

    def get(self, key: str) -> Optional[dict]:
        """
        Retrieve a cached receipt by transaction_id.

        Args:
            key: Transaction ID

        Returns:
            Cached receipt dict or None if not found/expired
        """
        if key not in self._cache:
            self._misses += 1
            logger.debug(f"Cache MISS for receipt {key}")
            return None

        receipt, timestamp = self._cache[key]

        # Check if entry has expired
        if time.time() - timestamp > self._ttl_seconds:
            del self._cache[key]
            self._expirations += 1
            self._misses += 1
            logger.info(f"Cache EXPIRED for receipt {key} (age: {int(time.time() - timestamp)}s)")
            return None

        # Move to end (LRU: most recently used)
        self._cache.move_to_end(key)
        self._hits += 1
        logger.debug(f"Cache HIT for receipt {key} (age: {int(time.time() - timestamp)}s)")
        return receipt

    def set(self, key: str, value: dict) -> None:
        """
        Store a receipt in the cache.

        Args:
            key: Transaction ID
            value: Receipt dictionary to cache
        """
        # Remove oldest entry if cache is full
        if key not in self._cache and len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            self._evictions += 1
            logger.debug(f"Cache EVICTION: removed {oldest_key} (LRU)")

        self._cache[key] = (value, time.time())
        logger.debug(f"Cache SET for receipt {key}")

    def invalidate(self, key: str) -> None:
        """
        Remove a specific receipt from the cache.

        Use this when a receipt is updated (e.g., refund processed)
        to ensure fresh data on next access.

        Args:
            key: Transaction ID to invalidate
        """
        if key in self._cache:
            del self._cache[key]
            logger.info(f"Cache INVALIDATED for receipt {key}")

    def clear(self) -> None:
        """Clear all cached receipts."""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Cache CLEARED: removed {count} entries")

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache performance statistics.

        Returns:
            Dictionary with hit rate, size, and other metrics
        """
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0.0

        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 2),
            "evictions": self._evictions,
            "expirations": self._expirations,
            "total_requests": total_requests,
        }


# Global cache instances (initialized once at app startup)
receipt_cache = ReceiptCache(max_size=500, ttl_seconds=900)  # 15 minutes TTL
customer_receipts_cache = ReceiptCache(max_size=200, ttl_seconds=300)  # 5 minutes TTL for lists
