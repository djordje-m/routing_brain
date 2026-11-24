"""Raw asyncpg SQL for the trucks/latest_positions snapshot.

Plain functions, not a class. Each takes `pool` as its first parameter —
no module-level pool access here (see `backend/app/db.py:get_pool`).
"""

import asyncpg


async def list_latest_trucks(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Return one row per truck that has a known latest position.

    A plain `JOIN` (not `LEFT JOIN`) is deliberate: a truck with no
    position yet has nothing displayable on a map and is simply omitted
    from the snapshot until its first position arrives.
    """
    return await pool.fetch(
        """
        SELECT t.id, t.name, t.type,
               ST_X(lp.geom) AS lon, ST_Y(lp.geom) AS lat,
               lp.speed, lp.heading, lp.recorded_at
        FROM trucks t
        JOIN latest_positions lp ON lp.truck_id = t.id
        ORDER BY t.id
        """
    )
