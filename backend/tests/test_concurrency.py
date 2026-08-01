"""The direct "show, don't assert" evidence for the core requirement: a seat
is never sold twice, even when many requests fight over it at the exact same
moment. Fires real concurrent requests at a real Postgres instance — no
mocking, no artificial serialization — and inspects both the API-level
outcomes and the raw DB state afterward.
"""

import asyncio
import time

from app.db import protocol
from app.errors import SeatContested, SeatUnavailable

CONCURRENCY = 60


async def test_only_one_winner_among_concurrent_holds_for_one_seat(pool, event_id, make_seat):
    seat_id = await make_seat()

    async def attempt(i: int):
        try:
            result = await protocol.create_hold(pool, event_id, [seat_id], f"session-{i}")
            return ("won", result)
        except (SeatUnavailable, SeatContested) as e:
            return ("lost", e)

    start = time.monotonic()
    outcomes = await asyncio.gather(*(attempt(i) for i in range(CONCURRENCY)))
    elapsed = time.monotonic() - start

    winners = [o for o in outcomes if o[0] == "won"]
    losers = [o for o in outcomes if o[0] == "lost"]
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}"
    assert len(losers) == CONCURRENCY - 1

    # DB state must agree with the API-level outcome: exactly one hold ever
    # got created for this seat, and the seat is HELD by exactly that hold.
    hold_count = await pool.fetchval(
        "SELECT count(*) FROM hold_seats WHERE seat_id = $1", seat_id
    )
    assert hold_count == 1
    seat = await pool.fetchrow("SELECT status, hold_id FROM seats WHERE id = $1", seat_id)
    assert seat["status"] == "HELD"
    assert seat["hold_id"] == winners[0][1].id

    # lock_timeout bounds worst-case wait; the whole burst should resolve in
    # well under the 3s lock_timeout even at this concurrency, since each
    # loser only queues behind a sub-millisecond winning transaction.
    assert elapsed < 3.0, f"took {elapsed:.2f}s — lock_timeout may not be working"


async def test_only_one_winner_among_concurrent_confirms_for_one_hold(pool, event_id, make_seat):
    """Guards the same invariant on the confirm path: even if a hold's owner
    double-clicks "confirm" (or retries a slow request), only one booking is
    ever created for it.
    """
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "session-1")

    async def attempt(i: int):
        try:
            booking = await protocol.confirm_hold(
                pool, hold.id, "session-1", "Ada", f"ada+{i}@example.com"
            )
            return ("won", booking)
        except Exception as e:
            return ("lost", e)

    outcomes = await asyncio.gather(*(attempt(i) for i in range(CONCURRENCY)))
    winners = [o for o in outcomes if o[0] == "won"]
    assert len(winners) == 1

    booking_count = await pool.fetchval(
        "SELECT count(*) FROM bookings WHERE hold_id = $1", hold.id
    )
    assert booking_count == 1


async def test_no_double_sell_across_many_seats_under_mixed_contention(pool, event_id, make_seat):
    """A closer-to-realistic scenario: many seats, many concurrent users each
    racing for a random small subset, some overlapping. No seat should end
    up with more than one hold/booking ever created for it.
    """
    import random

    seat_ids = [await make_seat(number=i) for i in range(10)]
    random.seed(42)

    async def buyer(i: int):
        picks = random.sample(seat_ids, k=random.choice([1, 2]))
        try:
            hold = await protocol.create_hold(pool, event_id, picks, f"session-{i}")
            await protocol.confirm_hold(
                pool, hold.id, f"session-{i}", f"Buyer {i}", f"buyer{i}@example.com"
            )
        except Exception:
            pass

    await asyncio.gather(*(buyer(i) for i in range(120)))

    rows = await pool.fetch(
        "SELECT id, (SELECT count(*) FROM hold_seats WHERE seat_id = seats.id) AS n "
        "FROM seats WHERE event_id = $1",
        event_id,
    )
    over_sold = [r for r in rows if r["n"] > 1]
    assert not over_sold, f"seats with more than one hold ever created: {over_sold}"
