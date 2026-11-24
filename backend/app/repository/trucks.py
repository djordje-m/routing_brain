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

    The `shifts` join is a `LEFT JOIN`: a truck somehow missing a shift
    row should still appear on the map, just with null shift fields.

    The `tow_calls` join is also a `LEFT JOIN`: a truck with no active
    (non-completed) assigned call should still appear, just with a null
    `active_call_id`.
    """
    return await pool.fetch(
        """
        SELECT t.id, t.name, t.type,
               ST_X(lp.geom) AS lon, ST_Y(lp.geom) AS lat,
               lp.speed, lp.heading, lp.recorded_at,
               s.start_time AS shift_start, s.end_time AS shift_end,
               tc.id AS active_call_id
        FROM trucks t
        JOIN latest_positions lp ON lp.truck_id = t.id
        LEFT JOIN shifts s ON s.truck_id = t.id
        LEFT JOIN tow_calls tc ON tc.assigned_truck_id = t.id AND tc.status != 'completed'
        ORDER BY t.id
        """
    )
