import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response

from app.db import protocol
from app.dependencies import get_pool, session_token
from app.errors import (
    HoldForbidden,
    HoldNotActive,
    HoldNotFound,
    SeatContested,
    SeatNotFound,
    SeatUnavailable,
)
from app.schemas import BookingOut, ConfirmHoldRequest, CreateHoldRequest, HoldOut

router = APIRouter(tags=["holds"])


@router.post("/events/{event_id}/holds", response_model=HoldOut, status_code=201)
async def create_hold(
    event_id: uuid.UUID,
    body: CreateHoldRequest,
    token: str = Depends(session_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if not body.seat_ids:
        raise HTTPException(400, "seat_ids must be non-empty")
    try:
        result = await protocol.create_hold(pool, event_id, body.seat_ids, token)
    except SeatNotFound as e:
        raise HTTPException(
            404, detail={"error": "seat_not_found", "seat_ids": _s(e.seat_ids)}
        ) from e
    except SeatUnavailable as e:
        raise HTTPException(
            409, detail={"error": "seat_unavailable", "seat_ids": _s(e.seat_ids)}
        ) from e
    except SeatContested as e:
        raise HTTPException(409, detail={"error": "seat_contested"}) from e
    return HoldOut(
        id=result.id,
        event_id=result.event_id,
        seat_ids=result.seat_ids,
        expires_at=result.expires_at,
    )


@router.post("/holds/{hold_id}/confirm", response_model=BookingOut)
async def confirm_hold(
    hold_id: uuid.UUID,
    body: ConfirmHoldRequest,
    token: str = Depends(session_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    try:
        result = await protocol.confirm_hold(
            pool, hold_id, token, body.customer_name, body.customer_email
        )
    except HoldNotFound as e:
        raise HTTPException(404, detail={"error": "hold_not_found"}) from e
    except HoldForbidden as e:
        raise HTTPException(403, detail={"error": "not_your_hold"}) from e
    except HoldNotActive as e:
        code = 410 if e.status == "EXPIRED" else 409
        raise HTTPException(code, detail={"error": "hold_not_active", "status": e.status}) from e
    except SeatContested as e:
        raise HTTPException(409, detail={"error": "seat_contested"}) from e
    return BookingOut(
        id=result.id, confirmation_code=result.confirmation_code, seat_ids=result.seat_ids
    )


@router.delete("/holds/{hold_id}", status_code=204)
async def cancel_hold(
    hold_id: uuid.UUID,
    token: str = Depends(session_token),
    pool: asyncpg.Pool = Depends(get_pool),
):
    try:
        await protocol.cancel_hold(pool, hold_id, token)
    except HoldNotFound as e:
        raise HTTPException(404, detail={"error": "hold_not_found"}) from e
    except HoldForbidden as e:
        raise HTTPException(403, detail={"error": "not_your_hold"}) from e
    except HoldNotActive as e:
        raise HTTPException(409, detail={"error": "hold_not_active", "status": e.status}) from e
    except SeatContested as e:
        raise HTTPException(409, detail={"error": "seat_contested"}) from e
    return Response(status_code=204)


def _s(ids: list[uuid.UUID]) -> list[str]:
    return [str(i) for i in ids]
