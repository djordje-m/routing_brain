from datetime import datetime, timedelta, timezone
from app.services.ingest import decide

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

def test_first_ever_position_is_accepted():
    assert decide(T0, None) == "accept"

def test_newer_than_latest_is_accepted():
    assert decide(T0 + timedelta(seconds=5), T0) == "accept"

def test_exact_same_timestamp_is_duplicate():
    assert decide(T0, T0) == "duplicate_noop"

def test_older_than_latest_is_stale_rejected():
    assert decide(T0 - timedelta(seconds=5), T0) == "stale_reject"
