# Decisions

Time spent: ~10 hours.

## The core problem, and the one decision everything else follows from

The hard part of this brief isn't CRUD on events/seats — it's that a seat must
never sell twice under real contention, *and* contention has to resolve fairly,
not arbitrarily. The decision that shapes the whole system: **Postgres is the
single lock manager and single source of truth, full stop.** No in-process
locks, no Redis lock, no application-level queue, anywhere. Every seat-mutating
operation (`app/db/protocol.py`) follows one protocol: `SELECT ... FOR UPDATE`
the seat row(s), re-read state under that lock, verify the precondition against
the fresh read, mutate, commit. No code path ever acts on state it observed
before acquiring the lock.

This one invariant is what buys three things at once:

1. **No double-sell**, trivially — mutual exclusion + re-verification means a
   second transaction targeting a held seat always sees the up-to-date state.
2. **Fairness for free.** Postgres's row-lock wait queue has been intentionally
   FIFO since 8.1, specifically to prevent starvation (verified against
   pgsql-hackers history, not just docs prose — Tom Lane, 2005: *"8.1 will
   guarantee first-come-first-served for row-level locks."*). Caveat I want to
   be precise about: this governs arrival order at Postgres's lock queue, not
   the original click — network and connection-pool queueing happen first, and
   correctness never actually depends on this ordering (only perceived
   fairness does — no-double-sell holds under any wait order). I also found one
   mailing-list report of out-of-FIFO-order delivery under concurrent-UPDATE
   during a wait, on Postgres 14; it doesn't apply here because every writer to
   a seat row goes through this same single protocol.
3. **Multi-instance safety close to free** (the stretch goal). N app instances
   issuing concurrent transactions against one Postgres is indistinguishable
   from N concurrent requests to one instance — there's nothing instance-local
   for correctness to depend on. This is why I attempted the stretch goal at
   all: it wasn't a separate mechanism bolted on, it fell out of the core
   design decision.

I rejected two more "standard" answers for this problem:

- **Optimistic concurrency (version column + CAS retry).** Legitimate in
  general, but worse here specifically: it has no queueing semantics at all —
  under a burst on one hot seat it's pure "first successful CAS wins," which is
  *closer* to arbitrary than the FIFO lock queue, directly against the
  brief's fairness requirement. It also adds real cost (retry/backoff,
  idempotency) for zero benefit, since these transactions are already
  microseconds long (single indexed-PK row, no external I/O).
- **Advisory locks.** Would work, but earn their keep locking a *concept* with
  no backing row. Here there's already exactly one row per seat to lock —
  advisory locks would just be an extra concept to explain for no gain.

## Concurrency protocol details worth flagging

- **`lock_timeout` (3s, `app/config.py`) is set on every transaction.** Without
  it, a hot-seat pileup holds a DB-pool connection per waiter for the whole
  wait, and a small pool (20 connections) can be exhausted by contention on
  *one* seat, taking down unrelated requests. A timeout turns that into a
  fast, honest 409 instead. Verified under load: 500 concurrent requests for
  one seat resolved in 3.68s wall-clock with exactly one winner
  (`scripts/load_test.py`), which is right at that boundary and confirms the
  bound is doing something, not just decorative.
- **Confirm checks `hold_id` equality, not `status = 'HELD'`.** A seat can be
  lazily reclaimed by a fresh `create_hold` before the sweeper ever marks the
  old hold `EXPIRED` — equality on the specific hold_id is what rejects a
  stale confirm in that case (see `still_valid` in `confirm_hold`).
- **The sweeper is pure liveness, not correctness.** `create_hold` and
  `confirm_hold` independently re-verify expiry under lock, so the system is
  correct even if the sweeper never runs — it exists purely so seats visibly
  free up (and waitlists get promoted) without requiring someone else to try
  the seat first. This split is asserted directly:
  `test_expired_seat_can_be_lazily_reclaimed_without_sweeper`.
- **Waitlist promotion uses plain `FOR UPDATE`, not `SKIP LOCKED`.** Waitlist
  rows are partitioned by `seat_id`, so there's no cross-seat contention to
  skip past — the only possible competitor for a given row is a user
  cancelling their own waitlist entry, a microsecond-scale conflict worth
  blocking on rather than risking a permanently skipped (not just delayed)
  waiter. I originally reached for `SKIP LOCKED` by reflex (it's the
  textbook answer for queue-draining) and corrected it after walking through
  the actual race — worth recording since it's the kind of thing that looks
  right and isn't.
- **A subtle bug I introduced and fixed:** raising a domain exception *inside*
  the `async with` transaction block makes asyncpg roll back the whole
  transaction — including mutations you want to keep (e.g. `confirm_hold`'s
  expired-hold path marks the hold `EXPIRED` and releases the seat, but still
  needs to reject the confirm request). Fix: never raise inside the block;
  compute a `pending_error`, let the block exit normally (commit), raise after.
  Documented in `CLAUDE.md` so it isn't reintroduced by copy-paste.

## Other architectural choices

- **Seat status lives on the row, not derived.** `seats.status` is the fast,
  join-free read path *and* the exact row every mutation locks — one place,
  not two things that can drift.
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
