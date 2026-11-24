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
