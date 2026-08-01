import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://seats:seats@localhost:5433/seats_test"
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=2, max_size=50)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _clean_db(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE bookings, hold_seats, waitlist, seats, holds, events RESTART IDENTITY CASCADE"
        )


@pytest.fixture
async def event_id(pool) -> uuid.UUID:
    eid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO events (id, name, venue, starts_at) VALUES ($1, $2, $3, $4)",
            eid,
            "Test Event",
            "Test Venue",
            datetime.now(UTC) + timedelta(days=1),
        )
    return eid


@pytest.fixture
def make_seat(pool, event_id):
    async def _make(section="A", row="1", number=1) -> uuid.UUID:
        seat_id = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO seats (id, event_id, section, row_label, seat_number)
                VALUES ($1, $2, $3, $4, $5)
                """,
                seat_id,
                event_id,
                section,
                row,
                number,
            )
        return seat_id

    return _make
