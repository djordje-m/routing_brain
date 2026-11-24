"""HTTP routes for the tow-call lifecycle: create, confirm, complete.

Pure orchestration -- all business logic (candidate prefiltering,
eligibility rechecking, ranking, ETA estimation) lives in
`app.repository.tow_calls` and `app.services.*`. This module only parses
requests, calls into those pieces, and shapes responses.
"""

import asyncpg
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
from app.services.eta import GoogleRoutesEtaError, get_eta_provider

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


async def _rejected_with_fresh_candidates(
    pool,
    required_truck_type: str,
    pickup_lat: float,
    pickup_lon: float,
    reason: str = "truck_no_longer_eligible",
) -> JSONResponse:
    """Build the 409 response body for "this confirm can't go through" --
    whether that's because the truck failed the early eligibility check,
    lost the atomic assignment race in `assign_truck`, or the tow call
    itself is no longer in a confirmable state. Always recomputes
    candidates fresh; never reuses a list computed earlier in the request.
    """
    fresh_candidates = await _compute_top_candidates(
        pool, required_truck_type, pickup_lat, pickup_lon
    )
    return JSONResponse(
        status_code=409,
        content={
            "status": "rejected",
            "reason": reason,
            "candidates": fresh_candidates,
        },
    )


@router.post("/tow-calls")
async def create_tow_call_endpoint(body: CreateTowCallRequest):
    pool = await get_pool()

    tow_call_id = await create_tow_call(
        pool, body.lat, body.lon, body.required_truck_type
    )
    try:
        candidates = await _compute_top_candidates(
            pool, body.required_truck_type, body.lat, body.lon
        )
    except GoogleRoutesEtaError as exc:
        # The tow call row above is already committed -- that's an
        # accepted, named gap (see README), not something this catch can
        # undo. But letting the ETA provider's failure propagate as an
        # unhandled 500 would otherwise leave the operator with a generic
        # error that repeats identically on every retry, with no signal
        # about what actually broke. Surface it as a readable 502 instead.
        raise HTTPException(
            status_code=502,
            detail=f"ETA provider failed while computing candidates: {exc}",
        ) from exc

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

    # "pending" is the only status confirm can act on. Without this guard,
    # a completed call could be resurrected back to "assigned" by a second
    # confirm (leaving its truck stuck en_route with no UI path to ever
    # complete it again), and an already-"assigned" call could be silently
    # reassigned to a different truck -- releasing the originally-assigned
    # truck with no record or error, since this is an UPDATE of the single
    # existing row rather than an insert the unique index would catch.
    if tow_call["status"] != "pending":
        # Reuse the same 409 shape (including a fresh `candidates` list)
        # as the other rejection path below, so any client that already
        # knows how to handle that shape -- e.g. the frontend's "stale
        # candidate list" branch -- degrades the same way here instead of
        # choking on a missing `candidates` field.
        return await _rejected_with_fresh_candidates(
            pool,
            required_truck_type,
            pickup_lat,
            pickup_lon,
            reason="tow_call_not_pending",
        )

    # Never trust that the candidate list shown at create time is still
    # valid -- recheck this specific truck against the DB right now.
    # Fast-path early-exit for the common, non-racing case (shift ended,
    # wrong type, already assigned as of this plain read). This check
    # alone does NOT close the race against another concurrent confirm
    # for the same truck -- it's a separate read before the write, so a
    # second request could still slip past it before either writes. The
    # actual guard against that is the atomic conditional UPDATE inside
    # assign_truck() below.
    eligible = await is_truck_still_eligible(
        pool, body.truck_id, required_truck_type
    )
    if not eligible:
        return await _rejected_with_fresh_candidates(
            pool, required_truck_type, pickup_lat, pickup_lon
        )

    # Truck is eligible -- recompute its ETA fresh via a direct location
    # lookup. Never trust a client-supplied ETA.
    truck_rows = await list_latest_trucks(pool)
    truck_row = next((r for r in truck_rows if r["id"] == body.truck_id), None)
    if truck_row is None:
        # Eligible per the DB query above but has no known position --
        # treat the same as "no longer usable" and hand back fresh
        # candidates rather than crash or assign a truck we can't locate.
        return await _rejected_with_fresh_candidates(
            pool, required_truck_type, pickup_lat, pickup_lon
        )

    provider = get_eta_provider()
    etas = await provider.estimate_etas(
        [(truck_row["lat"], truck_row["lon"])], (pickup_lat, pickup_lon)
    )
    eta_seconds = etas[0]

    # assign_truck()'s own WHERE clause re-checks "is this truck still
    # free" atomically with the write, closing the TOCTOU window the
    # is_truck_still_eligible() check above cannot close on its own. A
    # UNIQUE index backs this further still (see schema.sql) -- if that
    # index is what catches the race instead of the conditional UPDATE,
    # asyncpg raises UniqueViolationError, handled the same way below.
    try:
        assigned = await assign_truck(pool, tow_call_id, body.truck_id, eta_seconds)
    except asyncpg.UniqueViolationError:
        assigned = False

    if not assigned:
        return await _rejected_with_fresh_candidates(
            pool, required_truck_type, pickup_lat, pickup_lon
        )

    return {"status": "assigned", "truck_id": body.truck_id, "eta_seconds": eta_seconds}


@router.post("/tow-calls/{tow_call_id}/complete")
async def complete_tow_call_endpoint(tow_call_id: int):
    pool = await get_pool()

    tow_call = await get_tow_call(pool, tow_call_id)
    if tow_call is None:
        raise HTTPException(status_code=404, detail="tow call not found")

    await complete_tow_call(pool, tow_call_id)

    return {"status": "completed"}
