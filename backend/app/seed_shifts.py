"""Seed 20 drivers and their shifts, exactly once.

Unlike seed_trucks() (idempotent-by-upsert, re-asserts the same roster on
every restart), this seed is NOT idempotent-by-upsert: shift *timing* is
real data computed relative to now() at insert time, and real driver shifts
don't get regenerated just because a container restarted. So this checks
"has this ever run?" (any rows in `drivers`) and skips entirely if so.
"""

import logging

import asyncpg

logger = logging.getLogger(__name__)

DRIVER_NAMES = [
    "Marcus Reyes", "Dana Whitfield", "Carlos Jimenez", "Brittany Okafor",
    "Tyler Novak", "Priya Anand", "Devon Walsh", "Monica Pham",
    "Jordan Blake", "Aisha Thompson", "Ray Kowalski", "Nadia Silva",
    "Gavin Cho", "Teresa Nguyen", "Omar Haddad", "Kelly Sawyer",
    "Victor Dimitrov", "Sasha Mercer", "Leon Abara", "Wendy Park",
]

# Positions (0-indexed) in the zipped driver/truck order that get an
# already-ended shift instead of one bracketing now(). Position 0 ->
# truck-01, position 10 -> truck-11.
OFF_SHIFT_POSITIONS = {0, 10}


async def seed_drivers_and_shifts(pool: asyncpg.Pool) -> None:
    """Seed 20 drivers and their shifts, exactly once. Not idempotent-by-
    upsert like seed_trucks() -- shift *timing* is real data that shouldn't
    be regenerated on every restart, so this checks "has this ever run?"
    and skips entirely if so."""
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM drivers")
        if count:
            logger.info(
                "seed_drivers_and_shifts: drivers already seeded (%d rows), skipping",
                count,
            )
            return

        async with conn.transaction():
            driver_rows = await conn.fetch(
                """
                INSERT INTO drivers (name)
                VALUES ($1), ($2), ($3), ($4), ($5), ($6), ($7), ($8), ($9), ($10),
                       ($11), ($12), ($13), ($14), ($15), ($16), ($17), ($18), ($19), ($20)
                RETURNING id
                """,
                *DRIVER_NAMES,
            )
            driver_ids = [row["id"] for row in driver_rows]

            truck_rows = await conn.fetch(
                "SELECT id, traccar_device_id FROM trucks ORDER BY traccar_device_id"
            )
            truck_ids = [row["id"] for row in truck_rows]

            if len(driver_ids) != len(truck_ids):
                raise RuntimeError(
                    f"seed_drivers_and_shifts: expected {len(driver_ids)} trucks, "
                    f"found {len(truck_ids)} -- refusing to seed shifts"
                )

            pairs = list(zip(driver_ids, truck_ids))

            for position, (driver_id, truck_id) in enumerate(pairs):
                if position in OFF_SHIFT_POSITIONS:
                    await conn.execute(
                        """
                        INSERT INTO shifts (driver_id, truck_id, start_time, end_time)
                        VALUES ($1, $2, now() - interval '9 hours', now() - interval '1 hour')
                        """,
                        driver_id, truck_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO shifts (driver_id, truck_id, start_time, end_time)
                        VALUES ($1, $2, now() - interval '4 hours', now() + interval '4 hours')
                        """,
                        driver_id, truck_id,
                    )

        logger.info(
            "seed_drivers_and_shifts: seeded %d drivers and %d shifts",
            len(driver_ids), len(pairs),
        )
