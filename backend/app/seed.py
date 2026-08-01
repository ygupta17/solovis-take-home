"""Seeds a demo event with a seat grid. Run with `python -m app.seed`.

Idempotent: re-running against a DB that already has the demo event is a
no-op (keyed on event name), so it's safe to call from container startup or
by hand without worrying about duplicates.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from app.config import settings

EVENT_NAME = "The Midnight Sessions"
SECTIONS = [("Orchestra", 8, 12), ("Balcony", 4, 12)]  # (name, rows, seats_per_row)
ROW_LABELS = "ABCDEFGHIJKL"


async def seed() -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        existing = await conn.fetchval("SELECT id FROM events WHERE name = $1", EVENT_NAME)
        if existing:
            print(f"demo event already exists: {existing}")
            return

        event_id = uuid.uuid4()
        starts_at = datetime.now(UTC) + timedelta(days=30)
        await conn.execute(
            "INSERT INTO events (id, name, venue, starts_at) VALUES ($1, $2, $3, $4)",
            event_id,
            EVENT_NAME,
            "The Aurora Theater",
            starts_at,
        )

        seats = []
        for section, n_rows, n_seats in SECTIONS:
            for row_idx in range(n_rows):
                row_label = ROW_LABELS[row_idx]
                for seat_number in range(1, n_seats + 1):
                    seats.append((uuid.uuid4(), event_id, section, row_label, seat_number))

        await conn.executemany(
            """
            INSERT INTO seats (id, event_id, section, row_label, seat_number)
            VALUES ($1, $2, $3, $4, $5)
            """,
            seats,
        )
        print(f"seeded event {event_id} with {len(seats)} seats")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
