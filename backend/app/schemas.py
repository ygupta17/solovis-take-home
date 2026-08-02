import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator

# Letters (incl. common accented Latin-1 ones), spaces, hyphens, apostrophes,
# and periods — covers real names ("Mary-Jane", "O'Brien", "J. Smith")
# without accepting digits/symbols. Keep in sync with the mirrored check in
# web/src/SeatMap.tsx (client-side check is just for fast feedback; this is
# the actual enforcement).
NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'.\- ]+$")


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
    # Only ever set when the requester's own session owns this seat's hold
    # (null for everyone else's holds, including other people's HELD seats)
    # — see get_seat_map for why this exists (waitlist-promotion discovery).
    hold_id: uuid.UUID | None = None


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

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        if not NAME_PATTERN.match(v):
            raise ValueError(
                "Name can only contain letters, spaces, hyphens, apostrophes, and periods."
            )
        return v


class BookingOut(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    seat_ids: list[uuid.UUID]


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None
    seat_ids: list[uuid.UUID] | None = None
