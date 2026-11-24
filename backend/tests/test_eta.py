import asyncio

import pytest

from app.services import eta as eta_module
from app.services.eta import (
    DistanceBasedEta,
    GoogleRoutesEta,
    GoogleRoutesEtaError,
    get_eta_provider,
)

CHICAGO = (41.8781, -87.6298)
ONE_KM_NORTH = (41.8871, -87.6298)  # ~1000.75m due north at this latitude


def test_distance_based_eta_matches_known_distance():
    provider = DistanceBasedEta()
    etas = asyncio.run(provider.estimate_etas([ONE_KM_NORTH], CHICAGO))
    assert etas[0] == pytest.approx(120.0, rel=0.02)  # ~1000.75m at 30km/h


def test_factory_defaults_to_distance_based(monkeypatch):
    monkeypatch.delenv("GOOGLE_ROUTES_API_KEY", raising=False)
    assert isinstance(get_eta_provider(), DistanceBasedEta)


def test_factory_selects_google_when_key_present(monkeypatch):
    from app.services.eta import GoogleRoutesEta

    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")
    assert isinstance(get_eta_provider(), GoogleRoutesEta)


class _FakeResponse:
    """Minimal stand-in for `requests.Response`, only what the provider reads."""

    def __init__(self, status_code: int, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def test_google_routes_eta_matches_out_of_order_response_by_origin_index(
    monkeypatch,
):
    """The API's batch/stream nature means response elements aren't
    guaranteed to come back in request order. Feed the parser a response
    with originIndex 2, 0, 1 (reversed relative to the 3 input origins)
    and confirm the returned ETAs are still reordered back to match the
    *input* order, not the response order."""
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")

    out_of_order_elements = [
        {
            "originIndex": 2,
            "destinationIndex": 0,
            "status": {},
            "condition": "ROUTE_EXISTS",
            "duration": "300s",
        },
        {
            "originIndex": 0,
            "destinationIndex": 0,
            "status": {},
            "condition": "ROUTE_EXISTS",
            "duration": "100s",
        },
        {
            "originIndex": 1,
            "destinationIndex": 0,
            "status": {},
            "condition": "ROUTE_EXISTS",
            "duration": "200s",
        },
    ]
    monkeypatch.setattr(
        eta_module.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(200, out_of_order_elements),
    )

    provider = GoogleRoutesEta()
    etas = asyncio.run(
        provider.estimate_etas([(0, 0), (1, 1), (2, 2)], (9, 9))
    )

    assert etas == [100.0, 200.0, 300.0]


def test_google_routes_eta_raises_on_route_not_found(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")

    monkeypatch.setattr(
        eta_module.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            [
                {
                    "originIndex": 0,
                    "destinationIndex": 0,
                    "status": {},
                    "condition": "ROUTE_NOT_FOUND",
                }
            ],
        ),
    )

    provider = GoogleRoutesEta()
    with pytest.raises(GoogleRoutesEtaError):
        asyncio.run(provider.estimate_etas([(0, 0)], (9, 9)))


def test_google_routes_eta_raises_on_non_2xx_response(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")

    monkeypatch.setattr(
        eta_module.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(500, "server error"),
    )

    provider = GoogleRoutesEta()
    with pytest.raises(GoogleRoutesEtaError):
        asyncio.run(provider.estimate_etas([(0, 0)], (9, 9)))


def test_google_routes_eta_raises_on_error_status(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")

    monkeypatch.setattr(
        eta_module.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            [
                {
                    "originIndex": 0,
                    "destinationIndex": 0,
                    "status": {"code": 3, "message": "bad request"},
                    "condition": "ROUTE_EXISTS",
                    "duration": "100s",
                }
            ],
        ),
    )

    provider = GoogleRoutesEta()
    with pytest.raises(GoogleRoutesEtaError):
        asyncio.run(provider.estimate_etas([(0, 0)], (9, 9)))


def test_google_routes_eta_raises_on_missing_origin_in_response(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "fake-key-for-test")

    monkeypatch.setattr(
        eta_module.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            200,
            [
                {
                    "originIndex": 0,
                    "destinationIndex": 0,
                    "status": {},
                    "condition": "ROUTE_EXISTS",
                    "duration": "100s",
                }
            ],
        ),
    )

    provider = GoogleRoutesEta()
    with pytest.raises(GoogleRoutesEtaError):
        asyncio.run(provider.estimate_etas([(0, 0), (1, 1)], (9, 9)))
