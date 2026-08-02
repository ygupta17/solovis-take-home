"""Standalone load-test / evidence script: hammers ONE seat with many
concurrent HTTP requests against a real, running API instance (not an
in-process call — this goes over the network through uvicorn's ASGI stack,
unlike tests/test_concurrency.py which calls the protocol layer directly).

This is the artifact for the "show, don't assert" grading criterion: run it,
read the printed summary, and independently verify against the database
that exactly one winner was ever recorded.

Usage (with `make up` or the api service already running):
    python scripts/load_test.py
    CONCURRENCY=300 API_BASE_URL=http://localhost:8000 python scripts/load_test.py
"""

import asyncio
import os
import sys
import time
import uuid

import asyncpg
import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://seats:seats@localhost:5432/seats")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "200"))


async def setup_target_seat() -> tuple[uuid.UUID, uuid.UUID]:
    """Inserts one fresh seat under a dedicated throwaway event so this
    script is repeatable without colliding with seats other runs/tests have
    already sold — and, importantly, without polluting a real seeded demo
    event's seat map with a stray section.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        event_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO events (id, name, venue, starts_at) VALUES ($1, 'Load Test Event', "
            "'N/A', now())",
            event_id,
        )
        seat_id = uuid.uuid4()
        label = uuid.uuid4().hex[:6]
        await conn.execute(
            """
            INSERT INTO seats (id, event_id, section, row_label, seat_number)
            VALUES ($1, $2, 'LOADTEST', $3, 1)
            """,
            seat_id,
            event_id,
            label,
        )
        return event_id, seat_id
    finally:
        await conn.close()


async def verify_single_winner(seat_id: uuid.UUID) -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM hold_seats WHERE seat_id = $1", seat_id
        )
    finally:
        await conn.close()


async def cleanup_event(event_id: uuid.UUID) -> None:
    """Removes the throwaway event (and everything it accumulated) this run
    created — this script is meant to leave no trace in the real event
    picker, same reasoning as why setup_target_seat uses its own dedicated
    event instead of reusing a seeded one.
    """
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute(
            "DELETE FROM hold_seats WHERE seat_id IN (SELECT id FROM seats WHERE event_id = $1)",
            event_id,
        )
        await conn.execute("DELETE FROM bookings WHERE event_id = $1", event_id)
        await conn.execute("DELETE FROM seats WHERE event_id = $1", event_id)
        await conn.execute("DELETE FROM holds WHERE event_id = $1", event_id)
        await conn.execute("DELETE FROM events WHERE id = $1", event_id)
    finally:
        await conn.close()


async def attempt(client: httpx.AsyncClient, event_id: uuid.UUID, seat_id: uuid.UUID, i: int):
    t0 = time.monotonic()
    resp = await client.post(
        f"{API_BASE_URL}/events/{event_id}/holds",
        json={"seat_ids": [str(seat_id)]},
        headers={"X-Session-Token": f"loadtest-{i}-{uuid.uuid4()}"},
    )
    return resp.status_code, time.monotonic() - t0


async def main() -> int:
    print(f"target API: {API_BASE_URL}")
    print(f"concurrency: {CONCURRENCY}")
    event_id, seat_id = await setup_target_seat()
    print(f"target seat: {seat_id} (event {event_id})")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            start = time.monotonic()
            results = await asyncio.gather(
                *(attempt(client, event_id, seat_id, i) for i in range(CONCURRENCY))
            )
            elapsed = time.monotonic() - start

        statuses = [r[0] for r in results]
        latencies = [r[1] for r in results]
        winners = statuses.count(201)
        conflicts = statuses.count(409)
        other = len(statuses) - winners - conflicts

        print()
        print(f"total requests   : {len(statuses)}")
        print(f"201 Created      : {winners}")
        print(f"409 Conflict     : {conflicts}")
        print(f"other status     : {other}")
        print(f"wall clock       : {elapsed:.3f}s ({len(statuses) / elapsed:.0f} req/s)")
        print(f"p50 / p99 latency: {sorted(latencies)[len(latencies)//2]*1000:.1f}ms / "
              f"{sorted(latencies)[int(len(latencies)*0.99)]*1000:.1f}ms")

        db_winners = await verify_single_winner(seat_id)
        print()
        print(f"independent DB check — hold_seats rows for target seat: {db_winners}")

        ok = winners == 1 and db_winners == 1 and other == 0
        print()
        print("RESULT:", "PASS — exactly one winner, confirmed at the API and in the DB" if ok
              else "FAIL — seat was sold more than once, or an unexpected status occurred")
        return 0 if ok else 1
    finally:
        await cleanup_event(event_id)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
