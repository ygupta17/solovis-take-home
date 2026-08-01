import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


class EventOut(BaseModel):
    id: uuid.UUID
    name: str
    venue: str
    starts_at: datetime
    layout: Literal["theater", "stadium"]


class SeatOut(BaseModel):
    id: uuid.UUID
    section: str
    row_label: str
    seat_number: int
    status: str
    hold_expires_at: datetime | None = None


class CreateHoldRequest(BaseModel):
    seat_ids: list[uuid.UUID]


class HoldOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    seat_ids: list[uuid.UUID]
    expires_at: datetime


class ConfirmHoldRequest(BaseModel):
    customer_name: str
    customer_email: EmailStr


class BookingOut(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    seat_ids: list[uuid.UUID]


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None
    seat_ids: list[uuid.UUID] | None = None
