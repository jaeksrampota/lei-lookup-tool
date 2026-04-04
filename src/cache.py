"""Persistent SQLite-backed cache for API responses with TTL."""

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "cache.db"
_DEFAULT_TTL = 86400  # 24 hours


class Cache:
    """SQLite-backed persistent cache with TTL."""

    def __init__(self, db_path: Optional[Path] = None, ttl: float = _DEFAULT_TTL):
        self._db_path = db_path or _DEFAULT_CACHE_PATH
        self._ttl = ttl
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            conn.commit()

    @staticmethod
    def _key(prefix: str, params: dict) -> str:
        raw = prefix + json.dumps(params, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, prefix: str, params: dict) -> Optional[Any]:
        key = self._key(prefix, params)
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT value, created_at FROM cache WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                if time.time() - row[1] > self._ttl:
                    conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                    conn.commit()
                    return None
                return json.loads(row[0])
        except Exception:
            logger.debug("Cache read error", exc_info=True)
            return None

    def set(self, prefix: str, params: dict, value: Any) -> None:
        key = self._key(prefix, params)
        try:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                    (key, serialized, time.time()),
                )
                conn.commit()
        except Exception:
            logger.debug("Cache write error", exc_info=True)

    def clear(self) -> None:
        """Remove all cached entries."""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("DELETE FROM cache")
                conn.commit()
        except Exception:
            logger.debug("Cache clear error", exc_info=True)


# Global cache instance
cache = Cache()
