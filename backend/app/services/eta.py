"""ETA providers.

Two implementations of a common interface for estimating travel time
(in seconds) from a list of origin coordinates to a single destination:

- `DistanceBasedEta`: pure haversine-distance / constant-speed estimate.
  No network calls, no DB access -- used as the default/fallback provider.
- `GoogleRoutesEta`: calls Google's Routes API `computeRouteMatrix` endpoint.
  See that class's docstring for an important honesty note: it is
  implemented against Google's documented API shape but has never been
  exercised against a live API key in this project.

`get_eta_provider()` is a small factory that picks between the two based
on whether `GOOGLE_ROUTES_API_KEY` is set in the environment.
"""

import asyncio
import math
import os
from typing import Protocol

import requests

EARTH_RADIUS_M = 6371000
URBAN_AVERAGE_SPEED_MPS = 30 * 1000 / 3600  # 30 km/h


class EtaProvider(Protocol):
    async def estimate_etas(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
    ) -> list[float]: ...


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in meters."""
    lat1, lon1 = a
    lat2, lon2 = b

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    sin_a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(sin_a), math.sqrt(1 - sin_a))
    return EARTH_RADIUS_M * c


class DistanceBasedEta:
    """ETA provider using straight-line (haversine) distance at a fixed
    average urban speed. Pure computation -- no network or DB access."""

    async def estimate_etas(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
    ) -> list[float]:
        return [
            _haversine_m(origin, destination) / URBAN_AVERAGE_SPEED_MPS
            for origin in origins
        ]


GOOGLE_ROUTES_MATRIX_URL = (
    "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
)
GOOGLE_ROUTES_FIELD_MASK = "originIndex,destinationIndex,status,condition,duration"


class GoogleRoutesEtaError(RuntimeError):
    """Raised when the Google Routes API call fails or returns a response
    that cannot be trusted (non-2xx status, missing field, per-element
    error, or a route that could not be found).

    Deliberately a loud failure: a provider on an untested code path
    should never silently fall back to a zero or fabricated ETA.
    """


def _parse_duration_seconds(duration: str) -> float:
    """Parse a Duration-format string like "160s" into seconds.

    Raises `GoogleRoutesEtaError` if the value isn't the expected
    "<number>s" shape.
    """
    if not isinstance(duration, str) or not duration.endswith("s"):
        raise GoogleRoutesEtaError(
            f"Expected a duration string like '160s', got: {duration!r}"
        )
    try:
        return float(duration[:-1])
    except ValueError as exc:
        raise GoogleRoutesEtaError(
            f"Could not parse duration string: {duration!r}"
        ) from exc


class GoogleRoutesEta:
    """ETA provider backed by Google's Routes API `computeRouteMatrix`
    endpoint.

    HONESTY NOTE (implemented but unverified live): this implementation
    was written against Google's *documented* request/response shape,
    fetched live from
    https://developers.google.com/maps/documentation/routes/compute_route_matrix
    and
    https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRouteMatrix
    during development. No Google Maps Platform API key was available
    anywhere in this project or session, so this code has never been
    exercised against a real `computeRouteMatrix` call. Treat the request
    construction and, especially, the response parsing as unverified
    until someone runs it against a live key and confirms the real
    response matches what's assumed here.

    Confirmed from the live docs at the time of writing:
    - Endpoint: `POST https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix`
    - Headers: `Content-Type: application/json`, `X-Goog-Api-Key: <key>`,
      `X-Goog-FieldMask: originIndex,destinationIndex,status,condition,duration`
      -- the docs explicitly warn that `status` MUST be in the field mask,
      otherwise every element appears to be OK regardless of whether it
      actually succeeded.
    - Request body: `{"origins": [{"waypoint": {"location": {"latLng":
      {"latitude": ..., "longitude": ...}}}}], "destinations": [...],
      "travelMode": "DRIVE"}`.
    - Response body: a JSON array of `RouteMatrixElement` objects, one per
      origin/destination pair, each with `originIndex`, `destinationIndex`,
      `status` (a `google.rpc.Status`; `{}` means OK), `condition`
      (`ROUTE_EXISTS` or `ROUTE_NOT_FOUND`), and `duration` (a string like
      `"160s"`).
    """

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout_s = timeout_s

    async def estimate_etas(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
    ) -> list[float]:
        api_key = os.environ.get("GOOGLE_ROUTES_API_KEY")
        if not api_key:
            raise GoogleRoutesEtaError(
                "GOOGLE_ROUTES_API_KEY is not set; cannot call Google Routes API"
            )
        if not origins:
            return []

        request_body = {
            "origins": [
                {
                    "waypoint": {
                        "location": {
                            "latLng": {"latitude": lat, "longitude": lon}
                        }
                    }
                }
                for lat, lon in origins
            ],
            "destinations": [
                {
                    "waypoint": {
                        "location": {
                            "latLng": {
                                "latitude": destination[0],
                                "longitude": destination[1],
                            }
                        }
                    }
                }
            ],
            "travelMode": "DRIVE",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": GOOGLE_ROUTES_FIELD_MASK,
        }

        try:
            response = await asyncio.to_thread(
                requests.post,
                GOOGLE_ROUTES_MATRIX_URL,
                json=request_body,
                headers=headers,
                timeout=self._timeout_s,
            )
        except requests.RequestException as exc:
            raise GoogleRoutesEtaError(
                f"Request to Google Routes API failed: {exc}"
            ) from exc

        if not (200 <= response.status_code < 300):
            raise GoogleRoutesEtaError(
                f"Google Routes API returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            elements = response.json()
        except ValueError as exc:
            raise GoogleRoutesEtaError(
                f"Google Routes API response was not valid JSON: {exc}"
            ) from exc

        if not isinstance(elements, list):
            raise GoogleRoutesEtaError(
                f"Expected a JSON array of route matrix elements, got: "
                f"{type(elements).__name__}"
            )

        # The API returns one element per (origin, destination) pair, each
        # tagged with its own `originIndex`/`destinationIndex` -- the
        # response order is NOT guaranteed to match request order (it's a
        # streamed/batch result), so we must match back explicitly rather
        # than assume elements[i] corresponds to origins[i].
        etas_by_origin_index: dict[int, float] = {}

        for element in elements:
            if not isinstance(element, dict):
                raise GoogleRoutesEtaError(
                    f"Expected each route matrix element to be an object, got: "
                    f"{type(element).__name__}"
                )

            origin_index = element.get("originIndex", 0)
            destination_index = element.get("destinationIndex", 0)
            if not isinstance(origin_index, int) or not (
                0 <= origin_index < len(origins)
            ):
                raise GoogleRoutesEtaError(
                    f"Route matrix element has an invalid originIndex: "
                    f"{origin_index!r}"
                )
            if destination_index != 0:
                # We only ever send a single destination, so every
                # element must reference destination index 0.
                raise GoogleRoutesEtaError(
                    f"Expected destinationIndex 0 (single destination), got: "
                    f"{destination_index!r}"
                )

            # `status` must be in the field mask, and per Google's docs an
            # absent/empty status means OK (proto3 JSON omits zero-value
            # fields, so an OK `google.rpc.Status` serializes as `{}`).
            status = element.get("status")
            if status and status.get("code"):
                raise GoogleRoutesEtaError(
                    f"Route matrix element for origin {origin_index} "
                    f"returned an error status: {status!r}"
                )

            condition = element.get("condition")
            if condition != "ROUTE_EXISTS":
                raise GoogleRoutesEtaError(
                    f"Route matrix element for origin {origin_index} has "
                    f"condition {condition!r} (expected 'ROUTE_EXISTS') -- "
                    f"no usable route/duration was returned"
                )

            duration = element.get("duration")
            if duration is None:
                raise GoogleRoutesEtaError(
                    f"Route matrix element for origin {origin_index} is "
                    f"missing the 'duration' field"
                )

            etas_by_origin_index[origin_index] = _parse_duration_seconds(duration)

        missing = [i for i in range(len(origins)) if i not in etas_by_origin_index]
        if missing:
            raise GoogleRoutesEtaError(
                f"Google Routes API response is missing elements for origin "
                f"index(es): {missing}"
            )

        return [etas_by_origin_index[i] for i in range(len(origins))]


def get_eta_provider() -> EtaProvider:
    """Pick the ETA provider based on environment configuration.

    Uses `GoogleRoutesEta` when `GOOGLE_ROUTES_API_KEY` is set to a
    non-empty string; otherwise falls back to `DistanceBasedEta`.
    """
    if os.environ.get("GOOGLE_ROUTES_API_KEY"):
        return GoogleRoutesEta()
    return DistanceBasedEta()
