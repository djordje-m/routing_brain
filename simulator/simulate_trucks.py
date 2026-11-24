"""Truck position simulator: replays pre-baked real-street routes for the
20 bootstrapped Traccar devices (`truck-01`..`truck-20`) over the OsmAnd HTTP
protocol.

Each truck follows its own route from `simulator/routes.json` (one real
street-geometry route per truck, built in a prior milestone task) at a
constant speed derived from that route's own `distance_m` / `duration_s`.
Looping is "ping-pong": once a truck reaches the end of its route it reverses
and retraces the same polyline back to the start, then forward again, and so
on — it never teleports back to the start.

Verified against a live `traccar/traccar:latest` container on the OsmAnd
ingest port (5055):

- The ingest endpoint takes a plain HTTP GET with query parameters `id`,
  `lat`, `lon`, `timestamp`, `speed`, `bearing` — confirmed against both the
  public protocol docs (https://www.traccar.org/osmand/) and Traccar's
  `OsmAndProtocolDecoder` source, which also accepts `deviceid` as an alias
  for `id` and `heading` as an alias for `bearing`; this script uses the
  primary names.
- `timestamp` is accepted as Unix seconds: the decoder treats any numeric
  value less than `Integer.MAX_VALUE` (2147483647) as seconds and multiplies
  by 1000 internally. Current Unix time is well under that threshold, so
  plain `int(time.time())` is correct and future-proof for a long demo run.
- Unlike the admin REST API on port 8082 (which needs the bootstrapped
  admin credentials), the OsmAnd ingest endpoint on port 5055 requires no
  authentication at all — confirmed by sending an unauthenticated probe
  request and then reading it back via `GET /api/positions`.
- A position for a `uniqueId` that hasn't been registered as a device is
  silently dropped (no position is created, no error returned) rather than
  rejected with an HTTP error. This is exactly why `bootstrap` must finish
  registering all 20 devices before this service starts.
"""

import json
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException

TRACCAR_URL = os.environ.get("TRACCAR_OSMAND_URL", "http://traccar:5055")

TRUCK_IDS = [f"truck-{n:02d}" for n in range(1, 21)]

# Per-tick movement.
TICK_INTERVAL_SECONDS = 5

REQUEST_TIMEOUT_SECONDS = 5

EARTH_RADIUS_M = 6371000

# Manual pause/resume toggle (demo-operator convenience): a truck whose id is
# in `paused_trucks` is skipped entirely on each tick (no position computed,
# no request sent), so its last reported fixTime goes stale. Written from the
# FastAPI request-handling thread(s) via the routes below, read from the
# background sender thread's tick loop -- guarded by `paused_trucks_lock`.
paused_trucks: set[str] = set()
paused_trucks_lock = threading.Lock()

# Per-truck pause bookkeeping so a paused truck's virtual position freezes
# instead of silently continuing to advance against the shared `elapsed`
# clock. `pause_started_at` records when a truck was most recently paused
# (time.monotonic() value); `pause_offsets` accumulates the total seconds
# each truck has ever spent paused, which is subtracted from `elapsed`
# before calling `position_at_elapsed()`. Both are guarded by the same
# `paused_trucks_lock` used for `paused_trucks` -- no second lock needed.
pause_started_at: dict[str, float] = {}
pause_offsets: dict[str, float] = {}

ROUTES = json.load(open(Path(__file__).parent / "routes.json"))
ROUTES_BY_TRUCK = {r["truck_id"]: r for r in ROUTES}


def haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance between two lon/lat points, in meters."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def build_cumulative_distances(coordinates: list[list[float]]) -> list[float]:
    """Cumulative haversine distance (meters) along a route's coordinates."""
    cumulative = [0.0]
    for i in range(1, len(coordinates)):
        lon1, lat1 = coordinates[i - 1]
        lon2, lat2 = coordinates[i]
        cumulative.append(cumulative[-1] + haversine(lon1, lat1, lon2, lat2))
    return cumulative


CUMULATIVE_BY_TRUCK = {
    truck_id: build_cumulative_distances(route["coordinates"])
    for truck_id, route in ROUTES_BY_TRUCK.items()
}


def position_at_elapsed(
    route: dict, cumulative: list[float], elapsed_seconds: float
) -> tuple[float, float, float, float]:
    """Compute (lat, lon, bearing_degrees, speed_mps) for a route at a given
    elapsed time, replaying it at constant speed with ping-pong looping.

    Pure function: no I/O, no global state mutation.
    """
    distance_m = route["distance_m"]
    duration_s = route["duration_s"]
    coordinates = route["coordinates"]

    speed_mps = distance_m / duration_s

    cycle_length = 2 * distance_m
    raw_distance = speed_mps * elapsed_seconds
    pos_in_cycle = raw_distance % cycle_length
    forward_distance = (
        pos_in_cycle if pos_in_cycle <= distance_m else cycle_length - pos_in_cycle
    )

    # route["distance_m"] (the routing engine's stated total) and
    # cumulative[-1] (our own recomputed haversine sum over `coordinates`)
    # don't exactly agree -- for most routes distance_m is slightly larger.
    # forward_distance is bounded by distance_m above, so without this clamp
    # it can land past the last real coordinate (cumulative[-1]) on every
    # cycle, extrapolating lat/lon off the actual street geometry instead of
    # interpolating within it. Clamp it to the coordinates we actually have.
    forward_distance = min(forward_distance, cumulative[-1])

    # Linear search for the segment containing forward_distance. Route
    # point counts here are small (low hundreds), so this is cheap enough
    # per tick; a bisect would also work but isn't needed at this scale.
    segment_index = 0
    for i in range(1, len(cumulative)):
        if cumulative[i] >= forward_distance:
            segment_index = i - 1
            break
    else:
        segment_index = len(cumulative) - 2

    seg_start_distance = cumulative[segment_index]
    seg_end_distance = cumulative[segment_index + 1]
    seg_length = seg_end_distance - seg_start_distance

    lon1, lat1 = coordinates[segment_index]
    lon2, lat2 = coordinates[segment_index + 1]

    if seg_length > 0:
        fraction = (forward_distance - seg_start_distance) / seg_length
    else:
        fraction = 0.0

    lat = lat1 + (lat2 - lat1) * fraction
    lon = lon1 + (lon2 - lon1) * fraction

    # Reuse the existing atan2-based bearing convention from this project
    # (delta-lon, delta-lat), using the segment's own direction of travel.
    # On the return leg of the ping-pong, the segment is still walked from
    # its lower-distance endpoint to its higher-distance endpoint, so flip
    # the direction vector when travelling backward.
    if pos_in_cycle <= distance_m:
        d_lat = lat2 - lat1
        d_lon = lon2 - lon1
    else:
        d_lat = lat1 - lat2
        d_lon = lon1 - lon2
    bearing_degrees = math.degrees(math.atan2(d_lon, d_lat)) % 360

    return lat, lon, bearing_degrees, speed_mps


def send_position(truck_id: str, lat: float, lon: float, speed: float, bearing: float) -> None:
    params = {
        "id": truck_id,
        "lat": lat,
        "lon": lon,
        "timestamp": int(time.time()),
        "speed": speed,
        "bearing": bearing,
    }
    try:
        response = requests.get(TRACCAR_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            print(f"{truck_id}: unexpected HTTP {response.status_code}")
    except requests.exceptions.RequestException as exc:
        print(f"{truck_id}: failed to send position: {exc}")


def run_simulation_loop() -> None:
    """The per-tick send loop (from Task 2), run on a background thread.

    Movement math (`position_at_elapsed`) is untouched; the only addition is
    skipping a paused truck entirely -- no position is even computed for it.
    """
    print(f"Simulating {len(TRUCK_IDS)} trucks against {TRACCAR_URL} "
          f"every {TICK_INTERVAL_SECONDS}s.")

    start_time = time.monotonic()

    while True:
        elapsed = time.monotonic() - start_time
        for truck_id in TRUCK_IDS:
            with paused_trucks_lock:
                is_paused = truck_id in paused_trucks
                truck_pause_offset = pause_offsets.get(truck_id, 0.0)
            if is_paused:
                continue

            route = ROUTES_BY_TRUCK[truck_id]
            cumulative = CUMULATIVE_BY_TRUCK[truck_id]

            effective_elapsed = elapsed - truck_pause_offset
            lat, lon, bearing, speed = position_at_elapsed(route, cumulative, effective_elapsed)

            send_position(truck_id, lat, lon, speed, bearing)

        time.sleep(TICK_INTERVAL_SECONDS)


def main() -> None:
    """Retained for running the simulator without the HTTP app (not used by
    the Docker image, which runs it via uvicorn -- see Dockerfile)."""
    run_simulation_loop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=run_simulation_loop, daemon=True)
    thread.start()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/simulator/pause/{truck_id}")
async def pause_truck(truck_id: str):
    if truck_id not in TRUCK_IDS:
        raise HTTPException(status_code=404, detail=f"unknown truck_id: {truck_id}")
    with paused_trucks_lock:
        paused_trucks.add(truck_id)
        pause_started_at[truck_id] = time.monotonic()
    return {"status": "paused", "truck_id": truck_id}


@app.post("/simulator/resume/{truck_id}")
async def resume_truck(truck_id: str):
    with paused_trucks_lock:
        paused_trucks.discard(truck_id)
        started_at = pause_started_at.pop(truck_id, None)
        if started_at is not None:
            pause_duration = time.monotonic() - started_at
            pause_offsets[truck_id] = pause_offsets.get(truck_id, 0.0) + pause_duration
    return {"status": "resumed", "truck_id": truck_id}


if __name__ == "__main__":
    main()
