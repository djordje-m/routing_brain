"""One-shot Traccar bootstrap: waits for Traccar, then ensures 20 simulated
truck devices (`truck-01`..`truck-20`) exist.

Runs as its own compose service and exits. Idempotent — safe to run any
number of times; it only creates devices that don't already exist.

Verified against a live `traccar/traccar:latest` (6.17.0) container:

- `GET /api/server` requires no auth and responds immediately once the
  server is up; its `newServer` flag is `true` until the first user exists.
- There is no default `admin`/`admin` account. The *first* user ever
  created via `POST /api/users` becomes an administrator automatically,
  even though the server's own `registration` flag is `false` — this is
  how a fresh image bootstraps its only admin account. Every subsequent
  `POST /api/users` call fails with HTTP 400 ("Registration disabled"),
  which this script treats as "the account already exists" rather than
  a fatal error.
- HTTP Basic auth takes the user's `email` as the username, not `name`.
- `POST /api/devices` with `{"name": ..., "uniqueId": ...}` creates a
  device (200). Inserting a duplicate `uniqueId` fails with HTTP 400
  (a unique-constraint violation surfaced as a Java stack trace), which
  is why this script checks `GET /api/devices` first and only creates
  what's missing.
"""

import os
import sys
import time

import requests

TRACCAR_URL = os.environ.get("TRACCAR_URL", "http://traccar:8082")
ADMIN_EMAIL = os.environ.get("TRACCAR_ADMIN_EMAIL", "admin@routing-brain.local")
ADMIN_PASSWORD = os.environ.get("TRACCAR_ADMIN_PASSWORD", "routing-brain")

RETRY_INTERVAL_SECONDS = 2
RETRY_TIMEOUT_SECONDS = 60

TRUCK_IDS = [f"truck-{n:02d}" for n in range(1, 21)]


def wait_for_traccar() -> None:
    """Poll GET /api/server with backoff until Traccar responds."""
    deadline = time.monotonic() + RETRY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{TRACCAR_URL}/api/server", timeout=5)
            if response.status_code == 200:
                print("Traccar is up.")
                return
            last_error = RuntimeError(
                f"GET /api/server returned HTTP {response.status_code}"
            )
        except requests.exceptions.RequestException as exc:
            last_error = exc
        print(f"Traccar not ready yet ({last_error}); retrying...")
        time.sleep(RETRY_INTERVAL_SECONDS)
    raise SystemExit(
        f"Traccar did not become ready within {RETRY_TIMEOUT_SECONDS}s: {last_error}"
    )


def ensure_admin_account() -> None:
    """Create the admin account if this is a fresh Traccar instance.

    The first POST /api/users call on a fresh server creates an admin
    account with no auth required. On a server that already has a user
    (a rerun, or a restart with a persisted volume), the same call fails
    with HTTP 400 "Registration disabled" — that failure means the
    account already exists, so it is not treated as an error.
    """
    response = requests.post(
        f"{TRACCAR_URL}/api/users",
        json={
            "name": "admin",
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        },
        timeout=10,
    )
    if response.status_code == 200:
        print(f"Created admin account {ADMIN_EMAIL}.")
    else:
        print(
            f"Admin account creation returned HTTP {response.status_code} "
            f"(expected if {ADMIN_EMAIL} already exists); continuing with "
            "existing credentials."
        )


def existing_unique_ids(auth: tuple[str, str]) -> set[str]:
    response = requests.get(f"{TRACCAR_URL}/api/devices", auth=auth, timeout=10)
    response.raise_for_status()
    return {device["uniqueId"] for device in response.json()}


def create_device(auth: tuple[str, str], unique_id: str) -> None:
    response = requests.post(
        f"{TRACCAR_URL}/api/devices",
        auth=auth,
        json={"name": unique_id, "uniqueId": unique_id},
        timeout=10,
    )
    response.raise_for_status()
    print(f"Created device {unique_id}.")


def main() -> None:
    wait_for_traccar()
    ensure_admin_account()

    auth = (ADMIN_EMAIL, ADMIN_PASSWORD)
    present = existing_unique_ids(auth)

    created = 0
    skipped = 0
    for unique_id in TRUCK_IDS:
        if unique_id in present:
            skipped += 1
            continue
        create_device(auth, unique_id)
        created += 1

    print(
        f"Done: {created} device(s) created, {skipped} already present "
        f"({len(TRUCK_IDS)} total expected)."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - top-level guard for a one-shot script
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)
