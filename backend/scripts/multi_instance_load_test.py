"""Stretch-goal evidence: no-double-sell holds even when concurrent requests
for the SAME seat land on DIFFERENT app instances. Requires two API
instances already running against the same Postgres (see README.md for the
exact `docker run` commands used to stand up a second instance alongside
the docker-compose one).

Half the concurrent requests go to API_BASE_URL_1, half to API_BASE_URL_2 —
there is no coordination between them beyond the shared database, which is
exactly the point: correctness has to come from Postgres, not from anything
in-process.
"""

import asyncio
import os
import sys
import time
import uuid

import asyncpg
import httpx

API_BASE_URL_1 = os.environ.get("API_BASE_URL_1", "http://localhost:8000")
API_BASE_URL_2 = os.environ.get("API_BASE_URL_2", "http://localhost:8001")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://seats:seats@localhost:5432/seats")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "200"))


async def setup_target_seat() -> tuple[uuid.UUID, uuid.UUID]:
    """Uses a dedicated throwaway event, not an existing seeded one, so this
    script never leaves a stray section behind in a real demo event's seat
    map (see load_test.py for the same fix and why it matters)."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        event_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO events (id, name, venue, starts_at) VALUES ($1, 'Multi-Instance Load "
            "Test Event', 'N/A', now())",
            event_id,
        )
        seat_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO seats (id, event_id, section, row_label, seat_number)
            VALUES ($1, $2, 'MULTIINSTANCE', $3, 1)
            """,
            seat_id,
            event_id,
            uuid.uuid4().hex[:6],
        )
        return event_id, seat_id
    finally:
        await conn.close()


async def attempt(base_url: str, event_id: uuid.UUID, seat_id: uuid.UUID, i: int):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{base_url}/events/{event_id}/holds",
            json={"seat_ids": [str(seat_id)]},
            headers={"X-Session-Token": f"multi-{i}-{uuid.uuid4()}"},
        )
        return base_url, resp.status_code


async def main() -> int:
    print(f"instance 1: {API_BASE_URL_1}")
    print(f"instance 2: {API_BASE_URL_2}")
    print(f"concurrency: {CONCURRENCY} (split evenly across both instances)")
    event_id, seat_id = await setup_target_seat()
    print(f"target seat: {seat_id} (event {event_id})")

    start = time.monotonic()
    tasks = []
    for i in range(CONCURRENCY):
        base = API_BASE_URL_1 if i % 2 == 0 else API_BASE_URL_2
        tasks.append(attempt(base, event_id, seat_id, i))
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start

    by_instance: dict[str, list[int]] = {API_BASE_URL_1: [], API_BASE_URL_2: []}
    for base, status in results:
        by_instance[base].append(status)

    print()
    for base, statuses in by_instance.items():
        print(f"{base}: {len(statuses)} requests, {statuses.count(201)} winners, "
              f"{statuses.count(409)} conflicts")
    print(f"wall clock: {elapsed:.3f}s")

    total_winners = sum(s.count(201) for s in by_instance.values())

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        db_winners = await conn.fetchval(
            "SELECT count(*) FROM hold_seats WHERE seat_id = $1", seat_id
        )
    finally:
        await conn.close()

    print(f"\ntotal winners across both instances: {total_winners}")
    print(f"independent DB check — hold_seats rows for target seat: {db_winners}")

    ok = total_winners == 1 and db_winners == 1
    print()
    print(
        "RESULT:",
        "PASS — exactly one winner across two independent instances"
        if ok
        else "FAIL — seat was sold more than once across instances",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
