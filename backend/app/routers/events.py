import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_pool
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
async def get_seat_map(event_id: uuid.UUID, pool: asyncpg.Pool = Depends(get_pool)):
    """Plain, unlocked SELECT — deliberately does not participate in the
    FOR UPDATE protocol used by the write paths. Postgres MVCC means this
    read never blocks behind (or is blocked by) a hold/confirm transaction's
    row locks, which is how the seat map stays fast under heavy concurrent
    buying. It can be microseconds stale relative to an in-flight write,
    which is the "accurate-enough" the brief asks for — the write paths
    themselves are exact, since they re-verify under lock regardless of what
    any reader last saw. See DECISIONS.md.
    """
    rows = await pool.fetch(
        """
        SELECT id, section, row_label, seat_number, status, hold_expires_at
        FROM seats
        WHERE event_id = $1
        ORDER BY section_order, section, row_label, seat_number
        """,
        event_id,
    )
    return [SeatOut(**row) for row in rows]
