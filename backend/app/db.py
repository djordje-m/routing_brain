"""Database pool creation and schema application.

Owns exactly two things: the module-level asyncpg pool singleton
(`get_pool`), and applying `schema.sql` to the database (`apply_schema`).
No queries, no repository logic — those belong to later milestones.
"""

import os
from pathlib import Path

import asyncpg

_pool: asyncpg.Pool | None = None

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


async def get_pool() -> asyncpg.Pool:
    """Return the module-level asyncpg pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            database=os.environ["POSTGRES_DB"],
            host="db",
            port=5432,
        )
    return _pool


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Apply schema.sql against the given pool. Idempotent."""
    sql = SCHEMA_PATH.read_text()
    await pool.execute(sql)
