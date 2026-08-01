# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A seat reservation service (events, seats, holds, bookings) built for a take-home
assessment. The core engineering problem is concurrency correctness: a seat must
never be sold twice, even under heavy simultaneous contention, and contention must
resolve fairly. See `DECISIONS.md` for the full architecture rationale.

Stack: FastAPI + asyncpg (Python 3.12) backend, Postgres 16, React + TypeScript
(Vite) frontend, Docker Compose for local orchestration. No ORM — raw SQL via
asyncpg, deliberately, so the locking protocol below is fully visible rather than
hidden behind an abstraction.

## Commands

All backend commands run from `backend/` with the venv active:

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

- Start Postgres (dev + a disposable test instance on 5433): `docker compose up -d db db-test` (from repo root)
- Run the API locally: `DATABASE_URL=postgresql://seats:seats@localhost:5432/seats uvicorn app.main:app --reload`
- Seed the demo event: `python -m app.seed` (idempotent — safe to re-run)
- Run all tests: `TEST_DATABASE_URL=postgresql://seats:seats@localhost:5433/seats_test python -m pytest`
- Run a single test: `... python -m pytest tests/test_concurrency.py::test_only_one_winner_among_concurrent_holds_for_one_seat`
- Lint: `ruff check app tests` (`--fix` to auto-fix)
- Full stack via Docker: `docker compose up` (from repo root) — Postgres, API (auto-seeds on boot), and web

Tests require a **real** Postgres instance (`db-test`, not the dev `db`) — the
concurrency guarantees are a property of Postgres row locking, so mocking the DB
would test nothing meaningful. `tests/conftest.py` truncates all tables before every
test for isolation.

## Architecture: the concurrency protocol

Everything that matters lives in `app/db/protocol.py`. One invariant, applied
identically by every seat-mutating function (`create_hold`, `confirm_hold`,
`cancel_hold`, `sweep_expired_holds`, and the internal `_free_or_promote` waitlist
helper): **lock the relevant row(s) with `SELECT ... FOR UPDATE`, re-read current
state under that lock, verify the specific precondition against the fresh read,
mutate, commit.** No function ever acts on state it observed before acquiring the
lock. This is what makes the no-double-sell guarantee hold regardless of request
volume or app instance count — Postgres is the single lock manager and single
source of truth; there is no in-process lock or cache anywhere in this codebase for
correctness purposes.

Consequences of this design worth knowing before touching `protocol.py`:

- **`seats.status` is authoritative, not derived.** It lives directly on the row
  (not computed by joining `holds`/`bookings`) so reads are fast and so the row you
  need to lock is exactly the row you're checking.
- **Confirm checks `hold_id` equality, not just `status = 'HELD'`.** A seat can be
  lazily reclaimed by a fresh `create_hold` before the sweeper ever touches the old
  hold — equality on the specific hold_id is what rejects a stale confirm in that
  case. See the comment above `still_valid` in `confirm_hold`.
- **Never raise a domain exception from inside `_LockedTransaction`'s `async with`
  block if you want the mutation to commit anyway.** asyncpg rolls back the whole
  transaction on any exception escaping it. `confirm_hold`'s expired-hold path is
  the example to copy: it sets `pending_error`/`result` locals, lets the `async
  with` block exit normally (commit), and raises *after*.
- **The sweeper is pure liveness/UX, not correctness.** `create_hold` and
  `confirm_hold` independently re-verify expiry under lock, so the system is
  correct even if the sweeper never runs. It exists so seats visibly free up (and
  waitlists get promoted) without requiring someone else to try to grab the seat
  first. Safe to run from multiple instances concurrently — every release is
  guarded by `WHERE status = 'ACTIVE'`.
- **Waitlist promotion uses plain `FOR UPDATE`, not `SKIP LOCKED`.** Waitlist rows
  are partitioned by `seat_id`, so there's no cross-seat contention to skip past;
  the only possible competitor for a given row is a user cancelling their own
  waitlist entry, which is worth blocking on briefly rather than risking a
  permanent unfair skip. See the docstring on `_free_or_promote`.
- **`lock_timeout` (`app/config.py`, default 3s) is set on every transaction** via
  `_LockedTransaction`, and a timeout is translated into `SeatContested` rather than
  an unbounded hang — this bounds how long a hot-seat pileup can hold app-pool
  connections.

Live updates (`app/realtime/notify.py`) reuse the same "Postgres is the source of
truth" idea: `pg_notify()` fires inside the same transaction as each mutation, every
app instance LISTENs on a dedicated (non-pooled) connection, and WebSocket clients
are told to refetch the REST snapshot rather than trusting notify payloads as
ordered deltas (NOTIFY has no durability/redelivery guarantee — see the module
docstring).

## Layout

- `app/db/protocol.py` — the concurrency protocol (read this first).
- `app/routers/` — thin HTTP layer; maps `app/errors.py` exceptions to status
  codes and otherwise just calls into `protocol.py`.
- `app/workers/sweeper.py` — periodic expiry sweep loop, started in `main.py`'s
  lifespan.
- `app/realtime/notify.py` — LISTEN/NOTIFY → WebSocket fan-out.
- `db/init.sql` — schema, loaded automatically by the Postgres image on first
  container start. No migration framework (single-revision greenfield project;
  see `DECISIONS.md` for the trade-off).
- `tests/test_protocol.py` — state-machine tests, direct against `protocol.py`.
- `tests/test_concurrency.py` — the "show, don't assert" proof: real concurrent
  requests against real Postgres, asserting exactly one winner.
- `tests/test_waitlist.py` — fairness/ordering tests for waitlist promotion.
- `tests/test_api.py` — HTTP-level contract tests (status codes, JSON shapes).
