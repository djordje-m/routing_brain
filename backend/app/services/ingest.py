from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal


def decide(
    incoming_recorded_at: datetime, stored_latest_recorded_at: datetime | None
) -> Literal["accept", "stale_reject", "duplicate_noop"]:
    """
    Decide whether to accept an incoming position record based on its timestamp.

    Args:
        incoming_recorded_at: The timestamp of the incoming position record.
        stored_latest_recorded_at: The timestamp of the latest stored record, or None if no prior record exists.

    Returns:
        "accept" if the record should be accepted (first ever or newer than latest).
        "duplicate_noop" if the timestamp is identical to the latest.
        "stale_reject" if the timestamp is older than the latest.
    """
    if stored_latest_recorded_at is None:
        return "accept"

    if incoming_recorded_at == stored_latest_recorded_at:
        return "duplicate_noop"

    if incoming_recorded_at < stored_latest_recorded_at:
        return "stale_reject"

    return "accept"


async def apply_decision(
    outcome: Literal["accept", "stale_reject", "duplicate_noop"],
    insert: Callable[[], Awaitable[None]],
    upsert: Callable[[], Awaitable[None]],
) -> None:
    """Apply decide()'s outcome by calling the appropriate writer(s).

    duplicate_noop -> neither write. stale_reject -> insert only (history
    keeps the late point; latest_positions must not regress). accept ->
    both writes.
    """
    if outcome == "duplicate_noop":
        return
    elif outcome == "stale_reject":
        await insert()
    elif outcome == "accept":
        await insert()
        await upsert()
    else:
        raise ValueError(f"unexpected decide() outcome: {outcome!r}")
