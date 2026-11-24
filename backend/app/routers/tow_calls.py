"""HTTP routes for the tow-call lifecycle: create, confirm, complete.

Pure orchestration -- all business logic (candidate prefiltering,
eligibility rechecking, ranking, ETA estimation) lives in
`app.repository.tow_calls` and `app.services.*`. This module only parses
requests, calls into those pieces, and shapes responses.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import get_pool
from app.repository.trucks import list_latest_trucks
from app.repository.tow_calls import (
    assign_truck,
    complete_tow_call,
    create_tow_call,
    find_candidate_trucks,
    get_tow_call,
    is_truck_still_eligible,
)
from app.services.assignment import rank_candidates
from app.services.eta import get_eta_provider

router = APIRouter()


class CreateTowCallRequest(BaseModel):
    lat: float
    lon: float
    required_truck_type: str


class ConfirmTowCallRequest(BaseModel):
    truck_id: int


async def _compute_top_candidates(
    pool, required_truck_type: str, pickup_lat: float, pickup_lon: float
) -> list[dict]:
    """Run the full find -> estimate -> rank pipeline against current DB
    state. Used both by create (Step 1) and by confirm's rejection path
    (Step 2), which must return a genuinely fresh list, not the one shown
    at create time.
    """
    rows = await find_candidate_trucks(
        pool, required_truck_type, pickup_lat, pickup_lon
    )
    candidates = [
        {"truck_id": r["truck_id"], "name": r["name"], "lat": r["lat"], "lon": r["lon"]}
        for r in rows
    ]
    origins = [(c["lat"], c["lon"]) for c in candidates]

    provider = get_eta_provider()
    etas = await provider.estimate_etas(origins, (pickup_lat, pickup_lon))

    ranked = rank_candidates(candidates, etas, limit=3)
    return [
        {"truck_id": c["truck_id"], "name": c["name"], "eta_seconds": c["eta_seconds"]}
        for c in ranked
    ]


@router.post("/tow-calls")
async def create_tow_call_endpoint(body: CreateTowCallRequest):
    pool = await get_pool()

    tow_call_id = await create_tow_call(
        pool, body.lat, body.lon, body.required_truck_type
    )
    candidates = await _compute_top_candidates(
        pool, body.required_truck_type, body.lat, body.lon
    )

    return {"tow_call_id": tow_call_id, "candidates": candidates}


@router.post("/tow-calls/{tow_call_id}/confirm")
async def confirm_tow_call_endpoint(tow_call_id: int, body: ConfirmTowCallRequest):
    pool = await get_pool()

    tow_call = await get_tow_call(pool, tow_call_id)
    if tow_call is None:
        raise HTTPException(status_code=404, detail="tow call not found")

    required_truck_type = tow_call["required_truck_type"]
    pickup_lat = tow_call["lat"]
    pickup_lon = tow_call["lon"]

    # Never trust that the candidate list shown at create time is still
    # valid -- recheck this specific truck against the DB right now.
    eligible = await is_truck_still_eligible(
        pool, body.truck_id, required_truck_type
    )
    if not eligible:
        fresh_candidates = await _compute_top_candidates(
            pool, required_truck_type, pickup_lat, pickup_lon
        )
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": "truck_no_longer_eligible",
                "candidates": fresh_candidates,
            },
        )

    # Truck is eligible -- recompute its ETA fresh via a direct location
    # lookup. Never trust a client-supplied ETA.
    truck_rows = await list_latest_trucks(pool)
    truck_row = next((r for r in truck_rows if r["id"] == body.truck_id), None)
    if truck_row is None:
        # Eligible per the DB query above but has no known position --
        # treat the same as "no longer usable" and hand back fresh
        # candidates rather than crash or assign a truck we can't locate.
        fresh_candidates = await _compute_top_candidates(
            pool, required_truck_type, pickup_lat, pickup_lon
        )
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": "truck_no_longer_eligible",
                "candidates": fresh_candidates,
            },
        )

    provider = get_eta_provider()
    etas = await provider.estimate_etas(
        [(truck_row["lat"], truck_row["lon"])], (pickup_lat, pickup_lon)
    )
    eta_seconds = etas[0]

    await assign_truck(pool, tow_call_id, body.truck_id, eta_seconds)

    return {"status": "assigned", "truck_id": body.truck_id, "eta_seconds": eta_seconds}


@router.post("/tow-calls/{tow_call_id}/complete")
async def complete_tow_call_endpoint(tow_call_id: int):
    pool = await get_pool()

    tow_call = await get_tow_call(pool, tow_call_id)
    if tow_call is None:
        raise HTTPException(status_code=404, detail="tow call not found")

    await complete_tow_call(pool, tow_call_id)

    return {"status": "completed"}
