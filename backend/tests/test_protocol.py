"""State-machine tests for app/db/protocol.py, run against a real Postgres
instance (see conftest.py) — mocking the DB would defeat the point for a
concurrency-correctness problem where the DB *is* the mechanism under test.
"""

import uuid

import pytest

from app.db import protocol
from app.errors import (
    HoldForbidden,
    HoldNotActive,
    SeatUnavailable,
)


async def _seat_status(pool, seat_id) -> str:
    return await pool.fetchval("SELECT status FROM seats WHERE id = $1", seat_id)


async def test_create_hold_marks_seat_held(pool, event_id, make_seat):
    seat_id = await make_seat()
    result = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    assert result.seat_ids == [seat_id]
    assert await _seat_status(pool, seat_id) == "HELD"


async def test_second_hold_on_same_seat_is_rejected(pool, event_id, make_seat):
    seat_id = await make_seat()
    await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    with pytest.raises(SeatUnavailable) as exc:
        await protocol.create_hold(pool, event_id, [seat_id], "session-2")
    assert exc.value.seat_ids == [seat_id]


async def test_multi_seat_hold_is_all_or_nothing(pool, event_id, make_seat):
    seat_a = await make_seat(number=1)
    seat_b = await make_seat(number=2)
    await protocol.create_hold(pool, event_id, [seat_a], "session-1")

    with pytest.raises(SeatUnavailable) as exc:
        await protocol.create_hold(pool, event_id, [seat_a, seat_b], "session-2")
    assert exc.value.seat_ids == [seat_a]
    # seat_b must NOT have been held by the failed request's rollback.
    assert await _seat_status(pool, seat_b) == "AVAILABLE"


async def test_confirm_hold_marks_seat_sold_and_creates_booking(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    booking = await protocol.confirm_hold(pool, hold.id, "session-1", "Ada", "ada@example.com")
    assert booking.seat_ids == [seat_id]
    assert await _seat_status(pool, seat_id) == "SOLD"
    row = await pool.fetchrow(
        "SELECT confirmation_code FROM bookings WHERE id = $1", booking.id
    )
    assert row["confirmation_code"] == booking.confirmation_code


async def test_double_confirm_is_rejected(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    await protocol.confirm_hold(pool, hold.id, "session-1", "Ada", "ada@example.com")
    with pytest.raises(HoldNotActive) as exc:
        await protocol.confirm_hold(pool, hold.id, "session-1", "Ada", "ada@example.com")
    assert exc.value.status == "CONFIRMED"


async def test_confirm_with_wrong_session_is_forbidden(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    with pytest.raises(HoldForbidden):
        await protocol.confirm_hold(pool, hold.id, "session-2", "Eve", "eve@example.com")


async def test_confirm_after_expiry_is_rejected_and_seat_is_released(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1", ttl_seconds=-1)
    with pytest.raises(HoldNotActive) as exc:
        await protocol.confirm_hold(pool, hold.id, "session-1", "Ada", "ada@example.com")
    assert exc.value.status == "EXPIRED"
    # The rejection also released the seat in the same transaction — no
    # need to wait for the sweeper. See DECISIONS.md / protocol.py.
    assert await _seat_status(pool, seat_id) == "AVAILABLE"


async def test_cancel_hold_releases_seat(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    await protocol.cancel_hold(pool, hold.id, "session-1")
    assert await _seat_status(pool, seat_id) == "AVAILABLE"


async def test_cancel_hold_is_idempotent(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    await protocol.cancel_hold(pool, hold.id, "session-1")
    await protocol.cancel_hold(pool, hold.id, "session-1")  # must not raise
    assert await _seat_status(pool, seat_id) == "AVAILABLE"


async def test_cancel_confirmed_booking_is_rejected(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")
    await protocol.confirm_hold(pool, hold.id, "session-1", "Ada", "ada@example.com")
    with pytest.raises(HoldNotActive) as exc:
        await protocol.cancel_hold(pool, hold.id, "session-1")
    assert exc.value.status == "CONFIRMED"
    assert await _seat_status(pool, seat_id) == "SOLD"


async def test_sweeper_releases_expired_holds(pool, event_id, make_seat):
    seat_id = await make_seat()
    await protocol.create_hold(pool, event_id, [seat_id], "session-1", ttl_seconds=-1)
    swept = await protocol.sweep_expired_holds(pool)
    assert swept == 1
    assert await _seat_status(pool, seat_id) == "AVAILABLE"


async def test_sweeper_ignores_active_holds(pool, event_id, make_seat):
    seat_id = await make_seat()
    await protocol.create_hold(pool, event_id, [seat_id], "session-1", ttl_seconds=300)
    swept = await protocol.sweep_expired_holds(pool)
    assert swept == 0
    assert await _seat_status(pool, seat_id) == "HELD"


async def test_expired_seat_can_be_lazily_reclaimed_without_sweeper(pool, event_id, make_seat):
    """Correctness must not depend on the sweeper ever running — see
    protocol.py's module docstring."""
    seat_id = await make_seat()
    await protocol.create_hold(pool, event_id, [seat_id], "session-1", ttl_seconds=-1)
    result = await protocol.create_hold(pool, event_id, [seat_id], "session-2")
    assert result.seat_ids == [seat_id]
    assert await _seat_status(pool, seat_id) == "HELD"


async def test_create_hold_rejects_unknown_seat(pool, event_id):
    from app.errors import SeatNotFound

    with pytest.raises(SeatNotFound):
        await protocol.create_hold(pool, event_id, [uuid.uuid4()], "session-1")
