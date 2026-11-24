"""HTTP route for the trucks snapshot: GET /trucks/latest."""

from fastapi import APIRouter

from app.db import get_pool
from app.repository.trucks import list_latest_trucks

router = APIRouter()


@router.get("/trucks/latest")
async def get_latest_trucks():
    pool = await get_pool()
    rows = await list_latest_trucks(pool)
    return [
        {
            "truck_id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "lat": r["lat"],
            "lon": r["lon"],
            "speed": r["speed"],
            "heading": r["heading"],
            "recorded_at": r["recorded_at"].isoformat(),
            "shift_start": r["shift_start"].isoformat() if r["shift_start"] else None,
            "shift_end": r["shift_end"].isoformat() if r["shift_end"] else None,
        }
        for r in rows
    ]
