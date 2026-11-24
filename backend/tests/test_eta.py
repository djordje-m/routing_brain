import asyncio

import pytest

from app.services.eta import DistanceBasedEta, get_eta_provider

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
