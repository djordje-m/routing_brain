"""Raw asyncpg SQL for candidate-truck prefiltering and the `tow_calls` table.

Plain functions, not a class. Each takes `pool` as its first parameter —
no module-level pool access here (see `backend/app/db.py:get_pool`).
"""

import asyncpg


async def find_candidate_trucks(
    pool: asyncpg.Pool,
    required_truck_type: str,
    pickup_lat: float,
    pickup_lon: float,
    limit: int = 10,
) -> list[asyncpg.Record]:
    """Return up to `limit` eligible trucks, nearest-first by real distance.

    Eligible means: matching type, currently on shift, and not already
    assigned to a non-completed tow call. The `::geography` cast on both
    sides of `<->` is load-bearing: without it, `<->` on `geometry` orders
    by raw degree distance, which distorts badly east-west away from the
    equator. Casting to `geography` makes this a real-meters ordering.
    """
    return await pool.fetch(
        """
        SELECT t.id AS truck_id, t.name, ST_X(lp.geom) AS lon, ST_Y(lp.geom) AS lat
        FROM trucks t
        JOIN latest_positions lp ON lp.truck_id = t.id
        JOIN shifts s ON s.truck_id = t.id
        WHERE t.type = $1
          AND now() BETWEEN s.start_time AND s.end_time
          AND NOT EXISTS (
            SELECT 1 FROM tow_calls tc
            WHERE tc.assigned_truck_id = t.id AND tc.status != 'completed'
          )
        ORDER BY ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography <-> lp.geom::geography
        LIMIT $4
        """,
        required_truck_type,
        pickup_lon,
        pickup_lat,
        limit,
    )


async def is_truck_still_eligible(
    pool: asyncpg.Pool, truck_id: int, required_truck_type: str
) -> bool:
    """Re-check one truck against the same eligibility conditions as
    `find_candidate_trucks`, for use right before dispatch to catch a race
    against another assignment or a shift ending.
    """
    return await pool.fetchval(
        """
        SELECT EXISTS(
            SELECT 1
            FROM trucks t
            JOIN latest_positions lp ON lp.truck_id = t.id
            JOIN shifts s ON s.truck_id = t.id
            WHERE t.id = $1
              AND t.type = $2
              AND now() BETWEEN s.start_time AND s.end_time
              AND NOT EXISTS (
                SELECT 1 FROM tow_calls tc
                WHERE tc.assigned_truck_id = t.id AND tc.status != 'completed'
              )
        )
        """,
        truck_id,
        required_truck_type,
    )


async def create_tow_call(
    pool: asyncpg.Pool, lat: float, lon: float, required_truck_type: str
) -> int:
    """Insert a new pending tow call and return its id."""
    return await pool.fetchval(
        """
        INSERT INTO tow_calls (pickup_geom, required_truck_type, status)
        VALUES (ST_SetSRID(ST_MakePoint($1, $2), 4326), $3, 'pending')
        RETURNING id
        """,
        lon,
        lat,
        required_truck_type,
    )


async def get_tow_call(pool: asyncpg.Pool, tow_call_id: int) -> asyncpg.Record | None:
    """Fetch one tow call by id, or None if it doesn't exist."""
    return await pool.fetchrow(
        """
        SELECT id, ST_X(pickup_geom) AS lon, ST_Y(pickup_geom) AS lat,
               required_truck_type, status, assigned_truck_id
        FROM tow_calls
        WHERE id = $1
        """,
        tow_call_id,
    )


async def assign_truck(
    pool: asyncpg.Pool, tow_call_id: int, truck_id: int, eta_seconds: float
) -> None:
    """Mark a tow call as assigned to a truck with the given ETA."""
    await pool.execute(
        """
        UPDATE tow_calls
        SET assigned_truck_id = $1, status = 'assigned', eta_seconds = $2
        WHERE id = $3
        """,
        truck_id,
        eta_seconds,
        tow_call_id,
    )


async def complete_tow_call(pool: asyncpg.Pool, tow_call_id: int) -> None:
    """Mark a tow call as completed."""
    await pool.execute(
        "UPDATE tow_calls SET status = 'completed' WHERE id = $1",
        tow_call_id,
    )
