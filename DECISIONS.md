# Decisions


## Architecture

Stack: FastAPI + Python + asynpg backend, Postgres 16, React/TypeScript frontend,
Docker Compose for local orchestration.

**Why this stack:** Postgres has a built-in way to lock a single row so only one request can touch it at a time, and it hands that lock out in the order requests show up, which fits the requirement of "never sell a seat twice" and "be fair about who gets it" both need. That meant not having to build a custom locking system or bring in a caching technology like Redis just for this one problem.

FastAPI and asyncpg (the Python web framework and database driver) were 
a good fit on top of Postgres since they let the application handle people
grabbing seas at the same seat at once without getting stuck waiting on any one of them, and they let the code talk to Postgres directly in a way that keeps the locking logic easy to see and follow rather
than hiding it behind a layer that does things automatically. 

React with TypeScript on the frontend mainly helps keep track of the different states a seat can be in — open, held by someone, sold and Vite makes the dev experience fast. Docker Compose ties it all together so anyone can start the whole app with one command instead of installing Postgres and everything else by hand.

Postgres is the single lock manager and source of truth. There are no in-process locks, no Redis, no application-level queue, anywhere.
Every seat-mutating operation (`app/db/protocol.py`) follows one protocol:
`SELECT ... FOR UPDATE` the seat row(s), re-read state under that lock,
verify the precondition against the fresh read, mutate, commit.

That one invariant buys three things at once: **no double-sell** (mutual
exclusion + re-verification), **fairness for free** (Postgres's row-lock
wait queue is FIFO, so contention resolves in arrival order with no
app-level queue), and **multi-instance safety close to free** (N app
instances hitting one Postgres is indistinguishable from N requests to one
instance, since nothing correctness-relevant lives outside the database). Fairness here means arrival order at Postgres's lock queue, not the original click — correctness never depends on that ordering, only perceived fairness does.


## What the Postgres database holds

The database contains all the necessary fields and tables for the end to end seat reservation system. Here is a summary of the important components in the database:

Six tables, all in `db/init.sql`:

- **`events`** — `name`, `venue`, `starts_at`, and `layout` (`theater` |
  `stadium`).
- **`seats`** — one row per physical seat: `event_id`, `section`,
  `section_order` (depth-from-stage, set by the seeder, since Postgres can't
  infer that "Orchestra" is closer than "Balcony" from the name alone),
  `row_label`, `seat_number`, and **`status`** (`AVAILABLE` | `HELD` |
  `SOLD`) plus `hold_id` / `hold_expires_at` / `booking_id`. Status lives
  directly on this row rather than being derived by joining `holds`/
  `bookings` — it's both the fast, join-free read path for the seat map and
  the exact row every mutation locks with `SELECT ... FOR UPDATE`, so
  there's one place for state to live, not two things that can drift apart.
- **`holds`** — `event_id`, `session_token` (the client-generated identity,
  see Cuts), `status` (`ACTIVE` | `EXPIRED` | `CONFIRMED` | `CANCELLED`),
  and `expires_at`.
- **`hold_seats`** — join table, since one hold can cover several seats
  (`hold_id`, `seat_id`).
- **`bookings`** — the confirmed result of a hold: `confirmation_code`,
  `customer_name`, `customer_email`, plus the `event_id`/`hold_id` it came
  from.
- **`waitlist`** — `seat_id`, `session_token`, `created_at`; partitioned by
  seat so one seat's promotion queue never contends with another's.

## Concurrency protocol details worth flagging

- **`lock_timeout` (3s)** on every transaction turns a hot-seat pileup into
  a fast, honest 409 instead of exhausting the connection pool. Verified
  under load: 500 concurrent requests for one seat resolved in 3.68s
  wall-clock with exactly one winner — right at that boundary, confirming
  the bound is doing something, not just decorative.
- **Confirm checks `hold_id` equality, not just `status = 'HELD'`** — a
  seat can be lazily reclaimed by a fresh `create_hold` before the sweeper
  ever marks the old hold `EXPIRED`, and equality on the specific hold_id is
  what rejects a stale confirm in that case.
- **The sweeper is pure liveness, not correctness.** `create_hold` and
  `confirm_hold` independently re-verify expiry under lock, so the system
  stays correct even if the sweeper never runs — it exists so seats visibly
  free up without requiring someone else to try the seat first.
- **Waitlist promotion uses plain `FOR UPDATE`, not `SKIP LOCKED`** —
  waitlist rows are already partitioned by `seat_id`, so skipping would risk
  permanently passing over a waiter rather than just delaying them. I
  reached for `SKIP LOCKED` by reflex first (it's the textbook answer for
  queue-draining) and corrected it after walking through the actual race.
- **A subtle bug I introduced and fixed:** raising a domain exception
  *inside* the `async with` transaction block makes asyncpg roll back the
  whole transaction — including mutations meant to be kept. Fix: never
  raise inside the block; compute a `pending_error`, let the block exit
  normally (commit), raise after. Documented in `CLAUDE.md` so it isn't
  reintroduced by copy-paste.

## Other architectural choices

- **Reads are unlocked, plain `SELECT`s.** The seat map never touches `FOR
  UPDATE`, so Postgres MVCC means it's never blocked by (or blocking) an
  in-flight hold/confirm. It can be microseconds stale relative to a
  concurrent write, which is the "accurate-enough" the brief asks for — the
  write paths are exact regardless of what any reader last saw.
- **Live updates via Postgres `LISTEN`/`NOTIFY`, not in-process pub/sub.**
  Reuses the same "Postgres is the source of truth" idea for propagation:
  `pg_notify()` fires inside the mutating transaction, every instance LISTENs
  on a dedicated non-pooled connection, and — because NOTIFY has no
  durability or redelivery guarantee — every notify (and every reconnect) is
  treated as an *invalidation hint*, not a trusted ordered delta. Clients
  refetch a REST snapshot rather than applying a payload as a diff. This
  sidesteps an ordering/loss/replay problem I don't have hours to engineer
  around correctly, in exchange for a debounced extra fetch per change, which
  is cheap at this scale.
- **No ORM.** Raw SQL via asyncpg. The locking protocol is the product here;
  hiding it behind an ORM's abstraction would make the one thing worth
  reading harder to read, not easier.
- **Plain SQL schema file, not a migration framework.** One greenfield
  schema revision doesn't justify Alembic's overhead; a single `db/init.sql` a
  reviewer can read top-to-bottom is more legible for this scope. Trade-off:
  no versioned migration path if the schema needs to change post-launch —
  Alembic would be the obvious next step if this kept evolving.
- **No accounts/auth.** Session identity is a client-generated UUID in
  localStorage, sent as a header. Building real auth would have taken time
  away from the actual assessed problem without changing the concurrency
  story at all.

## Testing strategy

Every backend test runs against a **real** Postgres instance (a disposable
`db-test` service) — mocking the database would test nothing meaningful for a
problem whose entire correctness argument is "the database does the locking."

- `test_protocol.py` — state-machine coverage: every transition and rejection
  path (expiry, wrong session, double-confirm, cancel-after-confirm, lazy
  reclaim without the sweeper, all-or-nothing multi-seat holds).
- `test_concurrency.py` — the direct evidence for "never sold twice": tens of
  real concurrent coroutines racing for one seat/hold, asserting exactly one
  winner *and* independently checking the raw DB row count, plus a
  mixed-contention scenario across many seats and buyers.
- `test_waitlist.py` — promotion happens in arrival order, chains correctly
  through repeated releases, and leaving the waitlist only removes that entry.
- `test_api.py` — the HTTP contract on top of the above (status codes, error
  shapes, header handling).
- `scripts/load_test.py` and `scripts/multi_instance_load_test.py` — the
  "show, don't assert" artifacts: real HTTP requests against a **running**
  API (not in-process calls), independently checked against the database
  afterward. Run against the actual Docker stack for this submission:
  - 200 concurrent requests, single instance → 1 winner, 199 clean 409s,
    0.65s wall-clock, DB confirms exactly 1 `hold_seats` row.
  - 500 concurrent requests, single instance → 1 winner, 3.68s wall-clock
    (right at the `lock_timeout` boundary, confirming it's load-bearing).
  - **300 concurrent requests split across two independent API containers
    sharing one Postgres** (the multi-instance stretch) → 1 winner total, DB
    confirms exactly 1 row. Reproduce: `docker compose up -d`, then `docker
    run` a second `api` image on port 8001 pointed at the same `db` (exact
    command in README), then run the script with `CONCURRENCY=300`.
- Light manual browser verification of the actual UI flow (hold → countdown →
  confirm → seat map updates live) via a scripted Playwright pass, since this
  is a take-home and a full frontend test suite wasn't the highest-value use
  of remaining time (see Cuts).

## What I deliberately left out, and why

- **Cloud IaC.** The stack is fully containerized (Dockerfiles + Compose),
  which is the natural on-ramp to Terraform/a cloud provider, but I didn't
  spend the remaining time actually standing it up in the cloud — the
  concurrency problem was the higher-value use of the budget.
- **Payments.** "Confirm booking" is the checkout terminus; no real payment
  processing.
- **Refunds / seat returns after sale.** `SOLD` is terminal in this model — no
  cancellation-after-purchase flow, so the waitlist only ever gets promoted
  from a released *hold*, never from a returned booking.
- **Frontend test suite.** Verified manually (Playwright-scripted pass with
  screenshots) rather than with automated component/e2e tests, given the
  budget — the concurrency correctness is where the actual risk lives, and
  that's covered exhaustively on the backend.
- **A trusted delta stream for live updates.** Discussed above — invalidate +
  refetch instead, as a deliberate simplicity/correctness trade.
- **Real credential management.** `docker-compose.yml` hardcodes
  `seats`/`seats` for Postgres. That's intentional for a disposable local dev
  DB in a take-home (anyone who clones the repo gets a working instance with
  zero setup), but it's not something to carry into a real deployment —
  production would need secrets pulled from an env/secrets manager, not
  committed to the compose file.

## What I'd do differently with more time

- Automated frontend tests (React Testing Library) for the hold/confirm/expiry
  interaction, and a scripted browser-based concurrency demo (two tabs racing
  for the same seat) as a visual companion to the load-test scripts.
- Promote `db/init.sql` to Alembic once/if the schema needs to change, rather
  than waiting for that to become painful.
- Terraform + a real cloud deploy (ECS/Fly/Render) — the containerization is
  already there; this was purely a time cut, not a design gap.
- A small admin/seed UI instead of a Python script, so a reviewer doesn't need
  the backend venv just to create a demo event.
