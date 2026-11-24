"""FastAPI app: startup wiring (pool + schema), the health route, and the
Traccar position ingest route.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request

from app.db import apply_schema, get_pool
from app.repository.positions import (
    get_latest_recorded_at,
    get_truck_id_by_device,
    insert_position,
    upsert_latest_position,
)
from app.seed_trucks import seed_trucks
from app.services.ingest import decide

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await apply_schema(pool)
    await seed_trucks(pool)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ingest/position")
async def ingest_position(request: Request):
    """Receive a Traccar-forwarded position and route it through decide().

    Per Task 4's live-captured payload shape (forward.type=json), the body
    is a `PositionData` object: `{"position": {...}, "device": {...}}`.
    Relevant fields: `position.latitude`, `position.longitude`,
    `position.speed` (knots -- Traccar's convention, stored as-is, not
    converted -- see report), `position.course` (the heading; Traccar never
    calls it "heading"), `position.fixTime` (ISO-8601, used as
    `recorded_at`), and `device.uniqueId` (matches
    `trucks.traccar_device_id`).

    Never raises: Traccar retries on non-2xx, so any bad/malformed payload
    or DB hiccup here must be logged and swallowed, returning 200 anyway.
    """
    try:
        payload = await request.json()

        device_unique_id = payload["device"]["uniqueId"]
        position = payload["position"]
        lat = position["latitude"]
        lon = position["longitude"]
        speed = position.get("speed")
        heading = position.get("course")
        recorded_at = datetime.fromisoformat(position["fixTime"])

        pool = await get_pool()

        truck_id = await get_truck_id_by_device(pool, device_unique_id)
        if truck_id is None:
            logger.warning(
                "ingest/position: no truck found for device %r; dropping point",
                device_unique_id,
            )
            return {"status": "ignored", "reason": "unknown_device"}

        stored_latest_recorded_at = await get_latest_recorded_at(pool, truck_id)
        outcome = decide(recorded_at, stored_latest_recorded_at)

        if outcome == "duplicate_noop":
            pass
        elif outcome == "stale_reject":
            await insert_position(pool, truck_id, lat, lon, speed, heading, recorded_at)
        elif outcome == "accept":
            await insert_position(pool, truck_id, lat, lon, speed, heading, recorded_at)
            await upsert_latest_position(
                pool, truck_id, lat, lon, speed, heading, recorded_at
            )

        return {"status": "ok", "outcome": outcome}

    except Exception:
        logger.exception("ingest/position: failed to process incoming payload")
        return {"status": "error"}
