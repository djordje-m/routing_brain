"""Seed the trucks table with the 20-row minimal roster (pulled forward from Milestone 4)."""

import asyncpg


async def seed_trucks(pool: asyncpg.Pool) -> None:
    """Seed 20 truck rows into the trucks table. Idempotent."""
    rows = []

    # truck-01 to truck-10: light wreckers
    for n in range(1, 11):
        rows.append((f"truck-{n:02d}", f"Light Wrecker {n:02d}", "light"))

    # truck-11 to truck-16: heavy wreckers
    for n in range(11, 17):
        rows.append((f"truck-{n:02d}", f"Heavy Wrecker {n:02d}", "heavy"))

    # truck-17 to truck-20: flatbeds
    for n in range(17, 21):
        rows.append((f"truck-{n:02d}", f"Flatbed {n:02d}", "flatbed"))

    # Single multi-row INSERT with ON CONFLICT for idempotency
    insert_sql = """
        INSERT INTO trucks (traccar_device_id, name, type) VALUES
        (unnest($1::text[]), unnest($2::text[]), unnest($3::text[]))
        ON CONFLICT (traccar_device_id) DO NOTHING;
    """

    device_ids = [row[0] for row in rows]
    names = [row[1] for row in rows]
    types = [row[2] for row in rows]

    async with pool.acquire() as conn:
        await conn.execute(insert_sql, device_ids, names, types)
