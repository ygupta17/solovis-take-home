"""Stretch goal: when a held seat is released, waitlisted users get first
claim, in a defensible (FIFO arrival) order.
"""

import asyncio

import pytest

from app.db import protocol
from app.errors import CannotWaitlistOwnHold


async def test_waitlist_is_promoted_in_arrival_order_on_cancel(pool, event_id, make_seat):
    seat_id = await make_seat()
    holder_hold = await protocol.create_hold(pool, event_id, [seat_id], "holder")

    await protocol.join_waitlist(pool, seat_id, "waiter-1")
    await asyncio.sleep(0.02)  # keep created_at strictly ordered, not tie-broken
    await protocol.join_waitlist(pool, seat_id, "waiter-2")
    await asyncio.sleep(0.02)
    await protocol.join_waitlist(pool, seat_id, "waiter-3")

    await protocol.cancel_hold(pool, holder_hold.id, "holder")

    seat = await pool.fetchrow("SELECT status, hold_id FROM seats WHERE id = $1", seat_id)
    assert seat["status"] == "HELD"
    promoted_hold = await pool.fetchrow(
        "SELECT session_token FROM holds WHERE id = $1", seat["hold_id"]
    )
    assert promoted_hold["session_token"] == "waiter-1"

    # waiter-1 is off the list now; waiter-2 is still queued behind them.
    remaining = await pool.fetch(
        "SELECT session_token FROM waitlist WHERE seat_id = $1 ORDER BY created_at", seat_id
    )
    assert [r["session_token"] for r in remaining] == ["waiter-2", "waiter-3"]


async def test_waitlist_chains_through_multiple_releases(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold1 = await protocol.create_hold(pool, event_id, [seat_id], "holder")
    await protocol.join_waitlist(pool, seat_id, "waiter-1")
    await asyncio.sleep(0.02)
    await protocol.join_waitlist(pool, seat_id, "waiter-2")

    await protocol.cancel_hold(pool, hold1.id, "holder")
    seat = await pool.fetchrow("SELECT hold_id FROM seats WHERE id = $1", seat_id)
    hold2_id = seat["hold_id"]
    hold2_owner = await pool.fetchval("SELECT session_token FROM holds WHERE id = $1", hold2_id)
    assert hold2_owner == "waiter-1"

    await protocol.cancel_hold(pool, hold2_id, "waiter-1")
    seat = await pool.fetchrow("SELECT hold_id, status FROM seats WHERE id = $1", seat_id)
    hold3_owner = await pool.fetchval(
        "SELECT session_token FROM holds WHERE id = $1", seat["hold_id"]
    )
    assert hold3_owner == "waiter-2"

    # No one left waiting after the second promotion; releasing waiter-2's
    # hold too should finally leave the seat plainly AVAILABLE.
    await protocol.cancel_hold(pool, seat["hold_id"], "waiter-2")
    final = await pool.fetchrow("SELECT status, hold_id FROM seats WHERE id = $1", seat_id)
    assert final["status"] == "AVAILABLE"
    assert final["hold_id"] is None


async def test_leaving_waitlist_removes_only_that_entry(pool, event_id, make_seat):
    seat_id = await make_seat()
    hold = await protocol.create_hold(pool, event_id, [seat_id], "holder")
    await protocol.join_waitlist(pool, seat_id, "waiter-1")
    await protocol.join_waitlist(pool, seat_id, "waiter-2")

    await protocol.leave_waitlist(pool, seat_id, "waiter-1")

    await protocol.cancel_hold(pool, hold.id, "holder")
    seat = await pool.fetchrow("SELECT hold_id FROM seats WHERE id = $1", seat_id)
    owner = await pool.fetchval("SELECT session_token FROM holds WHERE id = $1", seat["hold_id"])
    assert owner == "waiter-2"


async def test_cannot_join_waitlist_for_seat_you_already_hold(pool, event_id, make_seat):
    """Reachable via a second tab of the same browser: same localStorage,
    hence same session_token, so the seat looks like a plain HELD seat to
    that tab even though it's the caller's own hold.
    """
    seat_id = await make_seat()
    await protocol.create_hold(pool, event_id, [seat_id], "holder")

    with pytest.raises(CannotWaitlistOwnHold):
        await protocol.join_waitlist(pool, seat_id, "holder")
