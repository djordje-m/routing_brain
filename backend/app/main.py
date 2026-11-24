"""FastAPI app: startup wiring (pool + schema), the health route, and the
Traccar position ingest route.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.db import apply_schema, get_pool
from app.repository.positions import (
    get_latest_recorded_at,
    get_truck_id_by_device,
    insert_position,
    upsert_latest_position,
)
from app.routers.trucks import router as trucks_router
from app.seed_trucks import seed_trucks
from app.services.ingest import apply_decision, decide
from app.ws import manager, websocket_endpoint

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await apply_schema(pool)
    await seed_trucks(pool)
    yield


app = FastAPI(lifespan=lifespan)

# Explicitly no-auth, short-lived demo: allow any origin so the frontend
# (served from a different host/port than the backend) can fetch and
# receive responses in a real browser. Not appropriate for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trucks_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_route(websocket: WebSocket):
    await websocket_endpoint(websocket)


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

        await apply_decision(
            outcome,
            insert=lambda: insert_position(
                pool, truck_id, lat, lon, speed, heading, recorded_at
            ),
            upsert=lambda: upsert_latest_position(
                pool, truck_id, lat, lon, speed, heading, recorded_at
            ),
        )

        if outcome == "accept":
            await manager.broadcast({
                "type": "position_update",
                "data": {
                    "truck_id": truck_id, "lat": lat, "lon": lon,
                    "speed": speed, "heading": heading,
                    "recorded_at": recorded_at.isoformat(),
                },
            })

        return {"status": "ok", "outcome": outcome}

    except Exception:
        logger.exception("ingest/position: failed to process incoming payload")
        return {"status": "error"}
