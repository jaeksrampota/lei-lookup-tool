"""SQLite persistence layer for the LEI Lookup Tool."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

import aiosqlite

from .models import InputEntity, LookupResult

# Default DB path: data/lei_lookup.db relative to project root
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "lei_lookup.db"

# Allow override via environment variable
DB_PATH: Path = Path(os.environ.get("LEI_DB_PATH", str(_DEFAULT_DB_PATH)))

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    results_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db_sync():
    """Create the database and table synchronously (for startup / tests)."""
    _ensure_dir()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(_CREATE_TABLE)
        conn.commit()


async def init_db():
    """Create the database and table asynchronously."""
    _ensure_dir()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()


async def save_job(
    job_id: str,
    filename: str,
    entities: list[InputEntity],
    status: str = "pending",
    results: list[LookupResult] | None = None,
    progress: int = 0,
    error_message: str = "",
    created_at: float = 0.0,
):
    """Insert a new job into the database."""
    entities_json = json.dumps([e.model_dump() for e in entities], ensure_ascii=False)
    results_json = json.dumps(
        [r.model_dump() for r in (results or [])], ensure_ascii=False, default=str
    )
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            """INSERT INTO jobs (id, filename, entities_json, results_json, status, progress, error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, filename, entities_json, results_json, status, progress, error_message, created_at),
        )
        await db.commit()


async def update_job_status(job_id: str, status: str, progress: int = 0, error_message: str = ""):
    """Update job status, progress, and error message."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "UPDATE jobs SET status = ?, progress = ?, error_message = ? WHERE id = ?",
            (status, progress, error_message, job_id),
        )
        await db.commit()


async def append_result(job_id: str, result: LookupResult, progress: int):
    """Append a single result to the job's results array and update progress.

    Uses a single UPDATE with json_insert to avoid read-modify-write race conditions.
    Falls back to read-modify-write inside a transaction if json_insert is unavailable.
    """
    result_json = json.dumps(result.model_dump(), ensure_ascii=False, default=str)
    async with aiosqlite.connect(str(DB_PATH), isolation_level="DEFERRED") as db:
        try:
            await db.execute(
                "UPDATE jobs SET results_json = json_insert(results_json, '$[#]', json(?)), progress = ? WHERE id = ?",
                (result_json, progress, job_id),
            )
        except Exception:
            # Fallback for SQLite versions without json_insert
            row = await db.execute_fetchall("SELECT results_json FROM jobs WHERE id = ?", (job_id,))
            if not row:
                return
            current = json.loads(row[0][0])
            current.append(result.model_dump())
            new_json = json.dumps(current, ensure_ascii=False, default=str)
            await db.execute(
                "UPDATE jobs SET results_json = ?, progress = ? WHERE id = ?",
                (new_json, progress, job_id),
            )
        await db.commit()


def _row_to_dict(row: tuple) -> dict:
    """Convert a DB row to a dictionary with parsed JSON fields."""
    return {
        "id": row[0],
        "filename": row[1],
        "entities": [InputEntity.model_validate(e) for e in json.loads(row[2])],
        "results": [LookupResult.model_validate(r) for r in json.loads(row[3])],
        "status": row[4],
        "progress": row[5],
        "error_message": row[6],
        "created_at": row[7],
    }


async def get_job(job_id: str) -> Optional[dict]:
    """Retrieve a job by ID. Returns None if not found."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        rows = await db.execute_fetchall(
            "SELECT id, filename, entities_json, results_json, status, progress, error_message, created_at FROM jobs WHERE id = ?",
            (job_id,),
        )
        if not rows:
            return None
        return _row_to_dict(rows[0])


async def get_all_jobs(limit: int = 50) -> list[dict]:
    """Retrieve recent jobs ordered by creation time descending."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        rows = await db.execute_fetchall(
            "SELECT id, filename, entities_json, results_json, status, progress, error_message, created_at FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [_row_to_dict(r) for r in rows]


async def count_jobs() -> int:
    """Count total jobs in the database."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) FROM jobs")
        return rows[0][0]


async def delete_all_jobs():
    """Delete all jobs. Used in tests."""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM jobs")
        await db.commit()
