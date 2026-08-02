import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_pool, session_token
from app.schemas import EventOut, SeatOut

router = APIRouter(tags=["events"])


@router.get("/events", response_model=list[EventOut])
async def list_events(pool: asyncpg.Pool = Depends(get_pool)):
    rows = await pool.fetch(
        "SELECT id, name, venue, starts_at, layout FROM events ORDER BY starts_at"
    )
    return [EventOut(**row) for row in rows]


@router.get("/events/{event_id}", response_model=EventOut)
async def get_event(event_id: uuid.UUID, pool: asyncpg.Pool = Depends(get_pool)):
    row = await pool.fetchrow(
        "SELECT id, name, venue, starts_at, layout FROM events WHERE id = $1", event_id
    )
    if row is None:
        raise HTTPException(404, "event not found")
    return EventOut(**row)


@router.get("/events/{event_id}/seats", response_model=list[SeatOut])
async def get_seat_map(
    event_id: uuid.UUID,
    pool: asyncpg.Pool = Depends(get_pool),
    token: str = Depends(session_token),
):
    """Plain, unlocked SELECT — deliberately does not participate in the
    FOR UPDATE protocol used by the write paths. Postgres MVCC means this
    read never blocks behind (or is blocked by) a hold/confirm transaction's
    row locks, which is how the seat map stays fast under heavy concurrent
    buying. It can be microseconds stale relative to an in-flight write,
    which is the "accurate-enough" the brief asks for — the write paths
    themselves are exact, since they re-verify under lock regardless of what
    any reader last saw. See DECISIONS.md.

    Joins in whether *this* requester's session owns each HELD seat's hold.
    Needed so a client that gets promoted off a waitlist (a hold created
    server-side, by someone else's cancel/expiry — the promoted client never
    called create_hold itself) has a way to discover "that HELD seat is
    actually now mine" from a plain refetch, instead of just seeing it as
    HELD the same as everyone else's holds.
    """
    rows = await pool.fetch(
        """
        SELECT s.id, s.section, s.row_label, s.seat_number, s.status, s.hold_expires_at,
               CASE WHEN h.session_token = $2 THEN s.hold_id END AS hold_id
        FROM seats s
        LEFT JOIN holds h ON h.id = s.hold_id
        WHERE s.event_id = $1
        ORDER BY s.section_order, s.section, s.row_label, s.seat_number
        """,
        event_id,
        token,
    )
    return [SeatOut(**row) for row in rows]
