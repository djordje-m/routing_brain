"""Stub truck position simulator: sends random-jitter GPS positions for the
20 bootstrapped Traccar devices (`truck-01`..`truck-20`) over the OsmAnd HTTP
protocol.

This is a deliberate throwaway stub for Milestone 1 — its only job is to get
positions flowing and visibly moving on the map. A later milestone rewrites
the movement logic in place to replay pre-baked real-street routes; the
device-ID scheme and the OsmAnd target stay the same.

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

import math
import os
import random
import time

import requests

TRACCAR_URL = os.environ.get("TRACCAR_OSMAND_URL", "http://traccar:5055")

TRUCK_IDS = [f"truck-{n:02d}" for n in range(1, 21)]

# Downtown Chicago.
BASE_LAT = 41.8781
BASE_LON = -87.6298

# Lay the 20 trucks out on a 4x5 grid around the base coordinate so they
# don't all start stacked on top of each other. ~0.01 degrees of latitude is
# roughly 1.1km, which keeps the whole fleet visible on a city-scale map.
GRID_COLUMNS = 5
GRID_SPACING_DEGREES = 0.01

# Per-tick movement.
TICK_INTERVAL_SECONDS = 5
JITTER_DEGREES = 0.0005

# Keep each truck wandering near its own grid slot rather than drifting
# unboundedly — this is a visual stub, not a route engine.
MAX_RADIUS_FROM_CENTER_DEGREES = 0.01

REQUEST_TIMEOUT_SECONDS = 5


def truck_center(index: int) -> tuple[float, float]:
    """Fixed per-truck base coordinate (grid offset from downtown Chicago)."""
    row, col = divmod(index, GRID_COLUMNS)
    lat = BASE_LAT + row * GRID_SPACING_DEGREES
    lon = BASE_LON + col * GRID_SPACING_DEGREES
    return lat, lon


def clamp_to_radius(
    lat: float, lon: float, center_lat: float, center_lon: float, max_radius: float
) -> tuple[float, float]:
    """Pull a candidate position back toward its center if it strayed too far.

    Plain degree-distance is good enough at this scale and latitude band —
    no need for real haversine math in a throwaway stub.
    """
    d_lat = lat - center_lat
    d_lon = lon - center_lon
    distance = (d_lat**2 + d_lon**2) ** 0.5
    if distance <= max_radius or distance == 0:
        return lat, lon
    scale = max_radius / distance
    return center_lat + d_lat * scale, center_lon + d_lon * scale


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


def main() -> None:
    print(f"Simulating {len(TRUCK_IDS)} trucks against {TRACCAR_URL} "
          f"every {TICK_INTERVAL_SECONDS}s.")

    # Current position and center per truck, keyed by index.
    centers = [truck_center(i) for i in range(len(TRUCK_IDS))]
    positions = list(centers)

    while True:
        for i, truck_id in enumerate(TRUCK_IDS):
            center_lat, center_lon = centers[i]
            lat, lon = positions[i]

            d_lat = random.uniform(-JITTER_DEGREES, JITTER_DEGREES)
            d_lon = random.uniform(-JITTER_DEGREES, JITTER_DEGREES)
            new_lat, new_lon = clamp_to_radius(
                lat + d_lat, lon + d_lon, center_lat, center_lon,
                MAX_RADIUS_FROM_CENTER_DEGREES,
            )
            positions[i] = (new_lat, new_lon)

            bearing = math.degrees(math.atan2(new_lon - lon, new_lat - lat)) % 360
            speed = random.uniform(5, 20)

            send_position(truck_id, new_lat, new_lon, speed, bearing)

        time.sleep(TICK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
