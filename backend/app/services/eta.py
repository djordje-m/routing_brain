"""ETA providers.

Two implementations of a common interface for estimating travel time
(in seconds) from a list of origin coordinates to a single destination:

- `DistanceBasedEta`: pure haversine-distance / constant-speed estimate.
  No network calls, no DB access -- used as the default/fallback provider.
- `GoogleRoutesEta`: stub for now; Task 2 fills in its body to call the
  Google Routes API.

`get_eta_provider()` is a small factory that picks between the two based
on whether `GOOGLE_ROUTES_API_KEY` is set in the environment.
"""

import math
import os
from typing import Protocol

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


class GoogleRoutesEta:
    """ETA provider backed by the Google Routes API.

    Stub for now -- Task 2 implements `estimate_etas`.
    """

    async def estimate_etas(
        self,
        origins: list[tuple[float, float]],
        destination: tuple[float, float],
    ) -> list[float]:
        raise NotImplementedError("GoogleRoutesEta is implemented in Task 2")


def get_eta_provider() -> EtaProvider:
    """Pick the ETA provider based on environment configuration.

    Uses `GoogleRoutesEta` when `GOOGLE_ROUTES_API_KEY` is set to a
    non-empty string; otherwise falls back to `DistanceBasedEta`.
    """
    if os.environ.get("GOOGLE_ROUTES_API_KEY"):
        return GoogleRoutesEta()
    return DistanceBasedEta()
