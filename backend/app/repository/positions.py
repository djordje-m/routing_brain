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

    `ON CONFLICT (truck_id, recorded_at) DO NOTHING` is a defensive safety
    net matching `decide()`'s `duplicate_noop` intent, in case this is ever
    called for a point `decide()` didn't actually clear — it shouldn't be,
    per Task 5, but the constraint existing is free insurance.
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
    is what actually prevents a regression; Task 5 is expected to only call
    this for `"accept"` outcomes, but the guard is free insurance here too.
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
