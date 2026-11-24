import asyncio
from datetime import datetime, timedelta, timezone

from app.services.ingest import apply_decision, decide

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

def test_first_ever_position_is_accepted():
    assert decide(T0, None) == "accept"

def test_newer_than_latest_is_accepted():
    assert decide(T0 + timedelta(seconds=5), T0) == "accept"

def test_exact_same_timestamp_is_duplicate():
    assert decide(T0, T0) == "duplicate_noop"

def test_older_than_latest_is_stale_rejected():
    assert decide(T0 - timedelta(seconds=5), T0) == "stale_reject"


class _Counter:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1


def test_duplicate_noop_calls_neither_writer():
    insert, upsert = _Counter(), _Counter()
    asyncio.run(apply_decision("duplicate_noop", insert=insert, upsert=upsert))
    assert insert.calls == 0
    assert upsert.calls == 0


def test_stale_reject_calls_insert_only():
    insert, upsert = _Counter(), _Counter()
    asyncio.run(apply_decision("stale_reject", insert=insert, upsert=upsert))
    assert insert.calls == 1
    assert upsert.calls == 0


def test_accept_calls_both_writers():
    insert, upsert = _Counter(), _Counter()
    asyncio.run(apply_decision("accept", insert=insert, upsert=upsert))
    assert insert.calls == 1
    assert upsert.calls == 1
