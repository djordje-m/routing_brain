"""Raw asyncpg SQL for the positions/latest_positions tables.

Plain functions, not a class. Each takes `pool` as its first parameter —
no module-level pool access here (see `backend/app/db.py:get_pool`).
"""

from datetime import datetime

import asyncpg


async def get_truck_id_by_device(
    pool: asyncpg.Pool, traccar_device_id: str
) -> int | None:
    """Look up a truck's internal id by its Traccar device id."""
    return await pool.fetchval(
        "SELECT id FROM trucks WHERE traccar_device_id = $1",
        traccar_device_id,
    )


async def get_latest_recorded_at(
    pool: asyncpg.Pool, truck_id: int
) -> datetime | None:
    """Return the `recorded_at` of a truck's latest known position, if any."""
    return await pool.fetchval(
        "SELECT recorded_at FROM latest_positions WHERE truck_id = $1",
        truck_id,
    )


async def insert_position(
    pool: asyncpg.Pool,
    truck_id: int,
    lat: float,
    lon: float,
    speed: float | None,
    heading: float | None,
    recorded_at: datetime,
) -> None:
    """Append a position to the `positions` history table.

    `ON CONFLICT (truck_id, recorded_at) DO NOTHING` is load-bearing, not a
    hypothetical safety net. `decide()` only ever compares against
    `latest_positions`, so a re-delivered duplicate of a point that is NOT
    the truck's current latest gets classified `stale_reject`, not
    `duplicate_noop` -- and `stale_reject` still calls this function. This
    `ON CONFLICT` is the only thing preventing that routine case from
    raising a real unique-constraint violation on `(truck_id, recorded_at)`.
    """
    await pool.execute(
        """
        INSERT INTO positions (truck_id, geom, speed, heading, recorded_at)
        VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326), $4, $5, $6)
        ON CONFLICT (truck_id, recorded_at) DO NOTHING
        """,
        truck_id,
        lon,
        lat,
        speed,
        heading,
        recorded_at,
    )


async def upsert_latest_position(
    pool: asyncpg.Pool,
    truck_id: int,
    lat: float,
    lon: float,
    speed: float | None,
    heading: float | None,
    recorded_at: datetime,
) -> None:
    """Upsert a truck's latest known position into `latest_positions`.

    The `WHERE latest_positions.recorded_at < EXCLUDED.recorded_at` clause
    is load-bearing, not optional polish. The real call sequence
    (`get_latest_recorded_at` -> `decide()` -> the writes) spans three
    separate round-trips with no transaction wrapping them, so two
    overlapping position forwards for the same truck can both read the
    same stored "latest" and both get classified `"accept"`. Without this
    `WHERE` clause, the older of the two writes could still land last and
    overwrite `latest_positions` with a stale point.
    """
    await pool.execute(
        """
        INSERT INTO latest_positions (truck_id, geom, speed, heading, recorded_at)
        VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326), $4, $5, $6)
        ON CONFLICT (truck_id) DO UPDATE SET
            geom = EXCLUDED.geom,
            speed = EXCLUDED.speed,
            heading = EXCLUDED.heading,
            recorded_at = EXCLUDED.recorded_at
        WHERE latest_positions.recorded_at < EXCLUDED.recorded_at
        """,
        truck_id,
        lon,
        lat,
        speed,
        heading,
        recorded_at,
    )
