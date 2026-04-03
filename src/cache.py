"""Simple in-memory cache for API responses."""

import hashlib
import json
from typing import Any, Optional


class Cache:
    """Thread-safe in-memory cache with optional TTL."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    @staticmethod
    def _key(prefix: str, params: dict) -> str:
        raw = prefix + json.dumps(params, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, prefix: str, params: dict) -> Optional[Any]:
        key = self._key(prefix, params)
        return self._store.get(key)

    def set(self, prefix: str, params: dict, value: Any) -> None:
        key = self._key(prefix, params)
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()


# Global cache instance
cache = Cache()
