"""One-time fetch script: bakes 20 real street-following routes from the
public OSRM demo server into `simulator/routes.json`.

This is NOT run by `docker compose up`, any Dockerfile, or the simulator at
runtime — it is a manual, one-time data-generation step. Task 2 (the
simulator's movement rewrite) consumes only the committed `routes.json` and
has zero runtime dependency on `router.project-osrm.org`.

Start/end points are deterministic (computed from a fixed formula, not
random) so this fetch is reproducible if ever re-run:

- Start = `truck_center(i)` from `simulate_trucks.py`'s Milestone 1 grid
  logic, reproduced here standalone since that file's movement logic is
  about to be rewritten by Task 2.
- End = start offset by bearing `(i * 18) % 360` degrees and a fixed
  distance of 2.0 km, using an equirectangular approximation (good enough at
  this scale/latitude — same justification `simulate_trucks.py` uses for its
  own flat-degree math).

Each of the 20 requests hits the shared public OSRM server once; we sleep
~0.5s between requests to be polite to it.
"""

import json
import math
import time
import urllib.error
import urllib.request

# Downtown Chicago -- must match simulate_trucks.py's BASE_LAT/BASE_LON.
BASE_LAT = 41.8781
BASE_LON = -87.6298

GRID_COLUMNS = 5
GRID_SPACING_DEGREES = 0.01

NUM_TRUCKS = 20
OFFSET_DISTANCE_KM = 2.0

OSRM_URL_TEMPLATE = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{start_lon},{start_lat};{end_lon},{end_lat}"
    "?overview=full&geometries=geojson"
)

REQUEST_SLEEP_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 15


def truck_center(index: int) -> tuple[float, float]:
    """Fixed per-truck base coordinate (grid offset from downtown Chicago).

    Reproduced from simulate_trucks.py's truck_center() -- same formula,
    kept standalone here since that file is about to be rewritten.
    """
    row, col = divmod(index, GRID_COLUMNS)
    lat = BASE_LAT + row * GRID_SPACING_DEGREES
    lon = BASE_LON + col * GRID_SPACING_DEGREES
    return lat, lon


def offset_point(lat: float, lon: float, bearing_degrees: float, distance_km: float) -> tuple[float, float]:
    """Equirectangular-approximation offset of (lat, lon) by a bearing/distance."""
    bearing_rad = math.radians(bearing_degrees)
    d_lat = (distance_km / 111.0) * math.cos(bearing_rad)
    d_lon = (distance_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(bearing_rad)
    return lat + d_lat, lon + d_lon


def fetch_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> dict:
    url = OSRM_URL_TEMPLATE.format(
        start_lon=start_lon, start_lat=start_lat, end_lon=end_lon, end_lat=end_lat
    )
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"OSRM returned no route: {data}")

    route = data["routes"][0]
    return {
        "coordinates": route["geometry"]["coordinates"],
        # OSRM occasionally returns a whole-number distance/duration with no
        # decimal point (e.g. `2773` instead of `2773.1`), which
        # json.loads() then parses as a Python int rather than float. Cast
        # explicitly so the committed file always matches the brief's
        # `<float>` shape.
        "distance_m": float(route["distance"]),
        "duration_s": float(route["duration"]),
    }


# A route is considered degenerate -- OSRM snapped the start and end to
# (effectively) the same road node, typically because the deterministic
# offset point landed in water (Lake Michigan, in this grid's case) -- when
# its actual distance comes back far shorter than the straight-line offset
# we asked for. When that happens we deterministically retry once with the
# bearing reversed (same distance), which walks back toward land instead of
# further into the water. This is documented per-pair below if it fires.
DEGENERATE_ROUTE_DISTANCE_RATIO = 0.5


def build_pair(index: int, bearing_degrees: float) -> tuple[float, float, float, float]:
    start_lat, start_lon = truck_center(index)
    end_lat, end_lon = offset_point(start_lat, start_lon, bearing_degrees, OFFSET_DISTANCE_KM)
    return start_lat, start_lon, end_lat, end_lon


def main() -> None:
    results = []

    for index in range(NUM_TRUCKS):
        truck_id = f"truck-{index + 1:02d}"
        bearing = (index * 18) % 360
        start_lat, start_lon, end_lat, end_lon = build_pair(index, bearing)

        route = None
        last_error = None
        bearings_to_try = [bearing, (bearing + 180) % 360]

        for attempt, try_bearing in enumerate(bearings_to_try):
            try_start_lat, try_start_lon, try_end_lat, try_end_lon = build_pair(index, try_bearing)
            try:
                candidate = fetch_route(try_start_lat, try_start_lon, try_end_lat, try_end_lon)
            except (RuntimeError, urllib.error.URLError) as exc:
                last_error = exc
                print(f"{truck_id}: attempt with bearing {try_bearing} failed: {exc}")
                time.sleep(REQUEST_SLEEP_SECONDS)
                continue

            expected_m = OFFSET_DISTANCE_KM * 1000
            if candidate["distance_m"] < expected_m * DEGENERATE_ROUTE_DISTANCE_RATIO:
                print(
                    f"{truck_id}: bearing {try_bearing} produced a degenerate route "
                    f"({candidate['distance_m']:.0f}m for a {expected_m:.0f}m request -- "
                    f"start/end likely snapped to the same node near water)"
                )
                if attempt == 0:
                    print(f"{truck_id}: retrying with bearing reversed to {bearings_to_try[1]}")
                    time.sleep(REQUEST_SLEEP_SECONDS)
                    continue
                # Reversed bearing was also degenerate -- keep it anyway
                # rather than looping forever; this did not occur in
                # practice (see task-1-report.md).
                route = candidate
                break

            if attempt > 0:
                print(
                    f"{truck_id}: recovered with reversed bearing {try_bearing} "
                    f"(original bearing {bearing} was degenerate near water)"
                )
            route = candidate
            break

        if route is None:
            raise RuntimeError(f"{truck_id}: all attempts failed, last error: {last_error}")

        results.append(
            {
                "truck_id": truck_id,
                "coordinates": route["coordinates"],
                "distance_m": route["distance_m"],
                "duration_s": route["duration_s"],
            }
        )
        print(
            f"{truck_id}: {len(route['coordinates'])} points, "
            f"{route['distance_m']:.0f}m, {route['duration_s']:.0f}s"
        )

        time.sleep(REQUEST_SLEEP_SECONDS)

    with open("routes.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"Wrote {len(results)} routes to routes.json")


if __name__ == "__main__":
    main()
