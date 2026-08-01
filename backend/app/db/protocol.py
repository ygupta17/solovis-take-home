"""The concurrency protocol: the one place every seat-mutating operation lives.

Invariant, applied identically by every function below (create_hold, confirm_hold,
cancel_hold, sweep_expired_hold, and the waitlist promotion helper): lock the
relevant row(s) with `SELECT ... FOR UPDATE`, re-read their current state under
that lock, verify the specific precondition against the fresh read, mutate,
commit. No function ever acts on state it observed before acquiring the lock.

This is what guarantees a seat is never sold twice, regardless of how many
requests arrive simultaneously or how many app instances are running — Postgres
is the single lock manager and the single source of truth; there is no
in-process lock or cache anywhere in this file. See DECISIONS.md for the full
argument (including why this also gives fair, FIFO-ish contention resolution
for free, and why lock_timeout is set on every transaction here).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from app.config import settings
from app.errors import (
    AlreadyOnWaitlist,
    HoldForbidden,
    HoldNotActive,
    HoldNotFound,
    SeatContested,
    SeatNotFound,
    SeatUnavailable,
)


@dataclass
class HoldResult:
    id: uuid.UUID
    event_id: uuid.UUID
    seat_ids: list[uuid.UUID]
    expires_at: datetime


@dataclass
class BookingResult:
    id: uuid.UUID
    confirmation_code: str
    seat_ids: list[uuid.UUID]


LOCK_NOT_AVAILABLE = "55P03"


class _LockedTransaction:
    """Context manager: opens a transaction with lock_timeout set.

    Translates a lock_timeout expiry into SeatContested instead of letting a
    raw Postgres error escape — a contended seat should fail fast and clean,
    not hang a caller or an app-pool connection indefinitely under a hot-seat
    pileup.
    """

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn
        self._tx = conn.transaction()

    async def __aenter__(self) -> asyncpg.Connection:
        await self._tx.__aenter__()
        await self._conn.execute(f"SET LOCAL lock_timeout = '{settings.lock_timeout}'")
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc is not None and isinstance(exc, asyncpg.PostgresError):
            if getattr(exc, "sqlstate", None) == LOCK_NOT_AVAILABLE:
                await self._tx.__aexit__(exc_type, exc, tb)
                raise SeatContested from exc
        return await self._tx.__aexit__(exc_type, exc, tb)


def _new_confirmation_code() -> str:
    return uuid.uuid4().hex[:8].upper()


async def create_hold(
    pool: asyncpg.Pool,
    event_id: uuid.UUID,
    seat_ids: list[uuid.UUID],
    session_token: str,
    ttl_seconds: int | None = None,
) -> HoldResult:
    ttl = ttl_seconds if ttl_seconds is not None else settings.hold_ttl_seconds
    ordered_ids = sorted(set(seat_ids))

    async with pool.acquire() as conn, _LockedTransaction(conn) as tx:
        rows = await tx.fetch(
            """
            SELECT id, status, hold_id, hold_expires_at
            FROM seats
            WHERE id = ANY($1::uuid[]) AND event_id = $2
            ORDER BY id
            FOR UPDATE
            """,
            ordered_ids,
            event_id,
        )
        if len(rows) != len(ordered_ids):
            found = {r["id"] for r in rows}
            raise SeatNotFound([s for s in ordered_ids if s not in found])

        now = await tx.fetchval("SELECT now()")
        unavailable = [
            r["id"]
            for r in rows
            if not (
                r["status"] == "AVAILABLE"
                or (r["status"] == "HELD" and r["hold_expires_at"] < now)
            )
        ]
        if unavailable:
            raise SeatUnavailable(unavailable)

        hold_id = uuid.uuid4()
        expires_at = now + _seconds(ttl)
        await tx.execute(
            """
            INSERT INTO holds (id, event_id, session_token, status, expires_at)
            VALUES ($1, $2, $3, 'ACTIVE', $4)
            """,
            hold_id,
            event_id,
            session_token,
            expires_at,
        )
        await tx.execute(
            """
            UPDATE seats
            SET status = 'HELD', hold_id = $1, hold_expires_at = $2, booking_id = NULL
            WHERE id = ANY($3::uuid[])
            """,
            hold_id,
            expires_at,
            ordered_ids,
        )
        await tx.executemany(
            "INSERT INTO hold_seats (hold_id, seat_id) VALUES ($1, $2)",
            [(hold_id, sid) for sid in ordered_ids],
        )
        await tx.execute("SELECT pg_notify('seat_updates', $1)", str(event_id))

        return HoldResult(
            id=hold_id, event_id=event_id, seat_ids=ordered_ids, expires_at=expires_at
        )


async def confirm_hold(
    pool: asyncpg.Pool,
    hold_id: uuid.UUID,
    session_token: str,
    customer_name: str,
    customer_email: str,
) -> BookingResult:
    async with pool.acquire() as conn, _LockedTransaction(conn) as tx:
        hold = await tx.fetchrow(
            "SELECT event_id, session_token, status FROM holds WHERE id = $1 FOR UPDATE",
            hold_id,
        )
        if hold is None:
            raise HoldNotFound
        if hold["session_token"] != session_token:
            raise HoldForbidden
        if hold["status"] != "ACTIVE":
            raise HoldNotActive(hold["status"])

        seat_rows = await tx.fetch(
            """
            SELECT s.id, s.status, s.hold_id, s.hold_expires_at
            FROM seats s
            JOIN hold_seats hs ON hs.seat_id = s.id
            WHERE hs.hold_id = $1
            ORDER BY s.id
            FOR UPDATE OF s
            """,
            hold_id,
        )
        now = await tx.fetchval("SELECT now()")
        seat_ids = [r["id"] for r in seat_rows]

        # Precondition is equality on *this specific* hold_id, not just
        # status='HELD' — this is what rejects a stale confirm for a hold
        # that's already been reclaimed by someone else. See DECISIONS.md.
        still_valid = all(
            r["status"] == "HELD" and r["hold_id"] == hold_id and r["hold_expires_at"] >= now
            for r in seat_rows
        )

        # NOTE: we deliberately do not `raise` in the branches below while
        # still inside the transaction — asyncpg rolls back the whole
        # transaction on any exception escaping it, which would also discard
        # the "mark EXPIRED and release the seat" mutation we *want* to keep
        # even though this particular request is being rejected. Instead we
        # commit normally and raise after exiting the `async with` block.
        pending_error: Exception | None = None
        result: BookingResult | None = None

        if not still_valid:
            await tx.execute(
                "UPDATE holds SET status = 'EXPIRED' WHERE id = $1 AND status = 'ACTIVE'",
                hold_id,
            )
            await _release_holds_seats(tx, hold_id)
            await tx.execute("SELECT pg_notify('seat_updates', $1)", str(hold["event_id"]))
            pending_error = HoldNotActive("EXPIRED")
        else:
            booking_id = uuid.uuid4()
            confirmation_code = _new_confirmation_code()
            await tx.execute(
                """
                INSERT INTO bookings (id, event_id, hold_id, confirmation_code,
                                       customer_name, customer_email)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                booking_id,
                hold["event_id"],
                hold_id,
                confirmation_code,
                customer_name,
                customer_email,
            )
            await tx.execute(
                """
                UPDATE seats
                SET status = 'SOLD', hold_id = NULL, hold_expires_at = NULL, booking_id = $1
                WHERE id = ANY($2::uuid[])
                """,
                booking_id,
                seat_ids,
            )
            await tx.execute("UPDATE holds SET status = 'CONFIRMED' WHERE id = $1", hold_id)
            await tx.execute("SELECT pg_notify('seat_updates', $1)", str(hold["event_id"]))
            result = BookingResult(
                id=booking_id, confirmation_code=confirmation_code, seat_ids=seat_ids
            )

    if pending_error is not None:
        raise pending_error
    assert result is not None
    return result


async def cancel_hold(pool: asyncpg.Pool, hold_id: uuid.UUID, session_token: str) -> None:
    async with pool.acquire() as conn, _LockedTransaction(conn) as tx:
        hold = await tx.fetchrow(
            "SELECT event_id, session_token, status FROM holds WHERE id = $1 FOR UPDATE",
            hold_id,
        )
        if hold is None:
            raise HoldNotFound
        if hold["session_token"] != session_token:
            raise HoldForbidden
        if hold["status"] == "CONFIRMED":
            raise HoldNotActive("CONFIRMED")
        if hold["status"] != "ACTIVE":
            return  # already EXPIRED/CANCELLED — idempotent no-op

        await tx.execute("UPDATE holds SET status = 'CANCELLED' WHERE id = $1", hold_id)
        await _release_holds_seats(tx, hold_id)
        await tx.execute("SELECT pg_notify('seat_updates', $1)", str(hold["event_id"]))


async def sweep_expired_holds(pool: asyncpg.Pool) -> int:
    """Runs periodically. Pure liveness/UX mechanism — see module docstring:
    correctness never depends on the sweeper's timing or even on it running
    at all, since create_hold/confirm_hold independently re-verify expiry.
    Safe to run from multiple app instances concurrently: each hold's release
    is guarded by `WHERE status = 'ACTIVE'`, so only one instance's UPDATE
    can ever affect a given hold.
    """
    candidate_ids = await pool.fetch(
        "SELECT id FROM holds WHERE status = 'ACTIVE' AND expires_at < now()"
    )
    swept = 0
    for row in candidate_ids:
        hold_id = row["id"]
        async with pool.acquire() as conn, _LockedTransaction(conn) as tx:
            claimed = await tx.fetchrow(
                """
                UPDATE holds SET status = 'EXPIRED'
                WHERE id = $1 AND status = 'ACTIVE' AND expires_at < now()
                RETURNING event_id
                """,
                hold_id,
            )
            if claimed is None:
                continue  # already confirmed/cancelled/expired by someone else
            await _release_holds_seats(tx, hold_id)
            await tx.execute("SELECT pg_notify('seat_updates', $1)", str(claimed["event_id"]))
            swept += 1
    return swept


async def _release_holds_seats(tx: asyncpg.Connection, hold_id: uuid.UUID) -> None:
    """Locks and releases every seat still owned by hold_id, promoting the
    front of each seat's waitlist if one exists. Must run inside a
    transaction whose caller has already transitioned the hold out of ACTIVE.
    """
    seat_rows = await tx.fetch(
        """
        SELECT s.id FROM seats s
        JOIN hold_seats hs ON hs.seat_id = s.id
        WHERE hs.hold_id = $1 AND s.hold_id = $1
        ORDER BY s.id
        FOR UPDATE OF s
        """,
        hold_id,
    )
    for row in seat_rows:
        await _free_or_promote(tx, row["id"])


async def _free_or_promote(tx: asyncpg.Connection, seat_id: uuid.UUID) -> None:
    """Caller must already hold the FOR UPDATE lock on this seat row.

    Deliberately plain FOR UPDATE, not FOR UPDATE SKIP LOCKED: waitlist rows
    are partitioned by seat_id, so there is no cross-seat contention to skip
    past. The only possible competitor for this specific row is a user
    cancelling their own waitlist entry — a microsecond-scale conflict worth
    blocking on. SKIP LOCKED would risk permanently passing over a waiter for
    this seat's one release event rather than just delaying them. See
    DECISIONS.md.
    """
    next_waiter = await tx.fetchrow(
        """
        SELECT id, session_token FROM waitlist
        WHERE seat_id = $1
        ORDER BY created_at, id
        LIMIT 1
        FOR UPDATE
        """,
        seat_id,
    )
    if next_waiter is None:
        await tx.execute(
            """
            UPDATE seats SET status = 'AVAILABLE', hold_id = NULL, hold_expires_at = NULL
            WHERE id = $1
            """,
            seat_id,
        )
        return

    await tx.execute("DELETE FROM waitlist WHERE id = $1", next_waiter["id"])
    event_id = await tx.fetchval("SELECT event_id FROM seats WHERE id = $1", seat_id)
    hold_id = uuid.uuid4()
    now = await tx.fetchval("SELECT now()")
    expires_at = now + _seconds(settings.hold_ttl_seconds)
    await tx.execute(
        """
        INSERT INTO holds (id, event_id, session_token, status, expires_at)
        VALUES ($1, $2, $3, 'ACTIVE', $4)
        """,
        hold_id,
        event_id,
        next_waiter["session_token"],
        expires_at,
    )
    await tx.execute(
        """
        UPDATE seats SET status = 'HELD', hold_id = $1, hold_expires_at = $2
        WHERE id = $3
        """,
        hold_id,
        expires_at,
        seat_id,
    )
    await tx.execute(
        "INSERT INTO hold_seats (hold_id, seat_id) VALUES ($1, $2)", hold_id, seat_id
    )


def _seconds(n: int) -> timedelta:
    return timedelta(seconds=n)


async def join_waitlist(pool: asyncpg.Pool, seat_id: uuid.UUID, session_token: str) -> None:
    """No FOR UPDATE needed: inserting a waitlist row doesn't touch the seat
    row at all, so there's nothing here for the seat-mutation protocol to
    race with. Worst case on a benign TOCTOU (seat becomes AVAILABLE the
    instant after this check) is the caller gets told to hold it directly
    but a moment later would've been auto-promoted anyway — not a
    correctness issue, just a UX nicety either way.
    """
    seat = await pool.fetchrow("SELECT status FROM seats WHERE id = $1", seat_id)
    if seat is None:
        raise SeatNotFound([seat_id])
    if seat["status"] == "AVAILABLE":
        raise SeatUnavailable([])  # reused as "not contested, just hold it"
    try:
        await pool.execute(
            "INSERT INTO waitlist (id, seat_id, session_token) VALUES ($1, $2, $3)",
            uuid.uuid4(),
            seat_id,
            session_token,
        )
    except asyncpg.UniqueViolationError as e:
        raise AlreadyOnWaitlist from e


async def leave_waitlist(pool: asyncpg.Pool, seat_id: uuid.UUID, session_token: str) -> None:
    """A plain DELETE already takes the row lock it needs. If this races
    with a release transaction's `_free_or_promote` (which does
    `SELECT ... FOR UPDATE` before deleting the same row to promote it),
    whichever gets there first wins cleanly: if promotion wins, this DELETE
    just affects 0 rows; if this wins, promotion finds no row and moves on
    to the next waiter (or frees the seat). No extra locking required.
    """
    await pool.execute(
        "DELETE FROM waitlist WHERE seat_id = $1 AND session_token = $2", seat_id, session_token
    )
