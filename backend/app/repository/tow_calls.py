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
) -> bool:
    """Atomically assign a truck to a tow call, but only if the truck is
    still free at the moment of the write.

    This closes the TOCTOU window between a separate "is this truck still
    eligible" check and a separate write: two concurrent confirm requests
    for the same truck against two different tow calls could otherwise
    both pass an eligibility check before either writes, then both
    assign -- double-dispatching one truck. Folding the check into the
    UPDATE's own WHERE clause means the "truck still free" condition and
    the write happen in one statement rather than two separate
    round-trips.

    A `UNIQUE` index (`tow_calls_one_active_assignment_per_truck` in
    schema.sql) on `assigned_truck_id WHERE status != 'completed'` is the
    real backstop underneath this: the conditional `NOT EXISTS` here is a
    plain, non-locking read of sibling rows, so on its own it is only as
    strong as per-statement MVCC visibility allows when two different
    tow-call rows race each other. The unique index makes Postgres itself
    refuse a second concurrent assignment via index-level conflict
    detection, independent of snapshot timing. If that index is violated
    (the rare race the `NOT EXISTS` check alone didn't catch in time),
    this raises `asyncpg.UniqueViolationError`, which the caller should
    treat the same as a `False` return.

    Returns True if the assignment happened, False if the truck was
    already assigned to some other non-completed tow call by the time
    this statement ran (i.e. lost the race, or failed the early
    eligibility check for an unrelated reason).
    """
    row = await pool.fetchrow(
        """
        UPDATE tow_calls
        SET assigned_truck_id = $1, status = 'assigned', eta_seconds = $2
        WHERE id = $3
          AND NOT EXISTS (
            SELECT 1 FROM tow_calls tc2
            WHERE tc2.assigned_truck_id = $1
              AND tc2.status != 'completed'
              AND tc2.id != $3
          )
        RETURNING id
        """,
        truck_id,
        eta_seconds,
        tow_call_id,
    )
    return row is not None


async def complete_tow_call(pool: asyncpg.Pool, tow_call_id: int) -> None:
    """Mark a tow call as completed."""
    await pool.execute(
        "UPDATE tow_calls SET status = 'completed' WHERE id = $1",
        tow_call_id,
    )
