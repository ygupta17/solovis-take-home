import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response

from app.db import protocol
from app.dependencies import get_pool, session_token
from app.errors import AlreadyOnWaitlist, SeatNotFound, SeatUnavailable

router = APIRouter(tags=["waitlist"])


@router.post("/seats/{seat_id}/waitlist", status_code=201)
async def join_waitlist(
    seat_id: uuid.UUID,
    token: str = Depends(session_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    try:
        await protocol.join_waitlist(pool, seat_id, token)
    except SeatNotFound as e:
        raise HTTPException(404, detail={"error": "seat_not_found"}) from e
    except SeatUnavailable as e:
        raise HTTPException(
            400,
            detail={"error": "seat_available", "detail": "seat is available, hold it directly"},
        ) from e
    except AlreadyOnWaitlist as e:
        raise HTTPException(409, detail={"error": "already_on_waitlist"}) from e
    return Response(status_code=201)


@router.delete("/seats/{seat_id}/waitlist", status_code=204)
async def leave_waitlist(
    seat_id: uuid.UUID,
    token: str = Depends(session_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    await protocol.leave_waitlist(pool, seat_id, token)
    return Response(status_code=204)
