"""Seeds a handful of demo events with varied venues and seat-map layouts.
Run with `python -m app.seed`.

Idempotent per event: re-running against a DB that already has an event of
a given name skips just that one, so it's safe to call from container
startup or by hand without worrying about duplicates, and safe to add more
events to EVENTS below later without re-creating the ones already seeded.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from app.config import settings

ROW_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# (section name, rows, seats per row)
Section = tuple[str, int, int]

EVENTS: list[dict] = [
    {
        "name": "The Midnight Sessions",
        "venue": "The Aurora Theater",
        "layout": "theater",
        "days_out": 30,
        "sections": [("Orchestra", 8, 12), ("Balcony", 4, 12)],
    },
    {
        "name": "Jazz Night at the Blue Room",
        "venue": "The Blue Room",
        "layout": "theater",
        "days_out": 10,
        "sections": [("Floor", 6, 8)],
    },
    {
        "name": "Solstice World Tour",
        "venue": "Meridian Arena",
        "layout": "stadium",
        "days_out": 60,
        "sections": [
            ("Floor", 2, 12),
            ("Lower Bowl North", 4, 12),
            ("Lower Bowl East", 4, 8),
            ("Lower Bowl South", 4, 12),
            ("Lower Bowl West", 4, 8),
            ("Upper Bowl", 5, 14),
        ],
    },
    {
        "name": "Championship Finals",
        "venue": "Ironclad Stadium",
        "layout": "stadium",
        "days_out": 45,
        "sections": [
            ("Home Side", 5, 14),
            ("North End", 3, 10),
            ("Away Side", 5, 14),
            ("South End", 3, 10),
        ],
    },
]


async def seed_event(
    conn: asyncpg.Connection, name: str, venue: str, layout: str, days_out: int,
    sections: list[Section],
) -> None:
    existing = await conn.fetchval("SELECT id FROM events WHERE name = $1", name)
    if existing:
        print(f"  already exists: {name} ({existing})")
        return

    event_id = uuid.uuid4()
    starts_at = datetime.now(UTC) + timedelta(days=days_out)
    await conn.execute(
        "INSERT INTO events (id, name, venue, starts_at, layout) VALUES ($1, $2, $3, $4, $5)",
        event_id,
        name,
        venue,
        starts_at,
        layout,
    )

    seats = []
    for section, n_rows, n_seats in sections:
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
    print(f"  seeded: {name} ({event_id}) — {len(seats)} seats, layout={layout}")


async def seed() -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        for event in EVENTS:
            await seed_event(
                conn,
                event["name"],
                event["venue"],
                event["layout"],
                event["days_out"],
                event["sections"],
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
