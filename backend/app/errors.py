"""Domain errors raised by the concurrency protocol (app/db/protocol.py).

Routers translate these into HTTP responses. Keeping them as distinct
exception types (rather than generic ValueError/dicts) means the mapping to
status codes lives in exactly one place (app/main.py) and every raise site
is explicit about which failure mode it means.
"""

import uuid


class SeatNotFound(Exception):
    def __init__(self, seat_ids: list[uuid.UUID]):
        self.seat_ids = seat_ids


class SeatUnavailable(Exception):
    """One or more requested seats are not AVAILABLE (or reclaimably-HELD)."""

    def __init__(self, seat_ids: list[uuid.UUID]):
        self.seat_ids = seat_ids


class SeatContested(Exception):
    """Row lock could not be acquired within lock_timeout."""


class HoldNotFound(Exception):
    pass


class HoldForbidden(Exception):
    """session_token on the request doesn't own this hold."""


class HoldNotActive(Exception):
    """Hold exists but is EXPIRED/CONFIRMED/CANCELLED, so it can't be confirmed."""

    def __init__(self, status: str):
        self.status = status


class AlreadyOnWaitlist(Exception):
    pass
