reqs not read

# Seat Reservation Service

A ticketing seat-reservation service: browse an event's live seat map, hold
seats while checking out, confirm a booking — with a hard guarantee that no
seat is ever sold twice, even under heavy concurrent contention, and even
across multiple app instances. See [DECISIONS.md](DECISIONS.md) for the
architecture rationale and trade-offs; [CLAUDE.md](CLAUDE.md) for a deeper
tour of the concurrency protocol if you're going to read the code.

## Stack

FastAPI + asyncpg (Python 3.12) · Postgres 16 · React + TypeScript (Vite) ·
Docker Compose.

## Run it

## MAC User
Requires Docker (with Compose).

## If docker is not installed
```bash
brew install --cask docker
```

```bash
nano ~/.zshrc
```
and add export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

```bash
source ~/.zshrc
```

```bash
docker compose up --build
```

## If docker is installed

```bash
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Web app: http://localhost:5173
- The API container seeds a demo event ("The Midnight Sessions", 144 seats
  across two sections) on startup — this is idempotent, safe to restart.

Stop with `docker compose down` (add `-v` to also wipe the Postgres volume —
without it, bookings survive a restart, which is the point).

## Run the backend/frontend natively (Postgres still needs to come from somewhere)

Only the API and web processes run natively here — you still need a Postgres
instance for them to talk to. Docker is the easiest way to get a disposable
one without installing Postgres directly; swap the first line for your own
instance (and `DATABASE_URL` below) if you'd rather not.

```bash
docker compose up -d db   # or point DATABASE_URL at a Postgres you already have

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
DATABASE_URL=postgresql://seats:seats@localhost:5432/seats python -m app.seed
DATABASE_URL=postgresql://seats:seats@localhost:5432/seats uvicorn app.main:app --reload

# Frontend, in another terminal
cd web
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

## Tests

```bash
docker compose up -d db-test   # disposable Postgres instance for tests
cd backend
source .venv/bin/activate      # after the venv setup above
TEST_DATABASE_URL=postgresql://seats:seats@localhost:5433/seats_test python -m pytest -v
```

All backend tests run against a real Postgres instance — see
[DECISIONS.md](DECISIONS.md) for why. `tests/test_concurrency.py` is the
direct evidence for "never sold twice": real concurrent coroutines racing for
one seat, checked against both the API responses and the raw database.

Or via `make test` / `make lint` (see [Makefile](Makefile)).

Frontend: `cd web && npm run lint && npm run typecheck && npm run build`.

CI (`.github/workflows/ci.yml`) runs all of the above, including the
concurrency test, against a real Postgres service container on every push.

## "Show, don't assert": load-test evidence

With the stack running (`docker compose up`), from `backend/` with the venv
active:

```bash
python scripts/load_test.py                    # 200 concurrent requests, one seat
CONCURRENCY=500 python scripts/load_test.py     # heavier
```

This fires real HTTP requests at the **running** API (not an in-process
call), then independently re-checks the database. Actual output from this
submission:

```
concurrency: 200 → 201 Created: 1, 409 Conflict: 199, 0.65s wall-clock
  independent DB check — hold_seats rows for target seat: 1 → PASS

concurrency: 500 → 201 Created: 1, 409 Conflict: 499, 3.68s wall-clock
  independent DB check — hold_seats rows for target seat: 1 → PASS
```

### Stretch goal: multi-instance safety

Prove the guarantee holds even when concurrent requests for the same seat
land on two independent app instances sharing one database:

```bash
docker compose up -d                     # instance 1, on :8000
docker run -d --name api-instance-2 \
  --network solovis-take-home_default \
  -e DATABASE_URL=postgresql://seats:seats@db:5432/seats \
  -e HOLD_TTL_SECONDS=90 -p 8001:8000 \
  solovis-take-home-api \
  uvicorn app.main:app --host 0.0.0.0 --port 8000   # instance 2, on :8001

cd backend && source .venv/bin/activate
CONCURRENCY=300 python scripts/multi_instance_load_test.py
docker rm -f api-instance-2   # cleanup
```

Actual output from this submission:

```
instance 1: 150 requests, 1 winners, 149 conflicts
instance 2: 150 requests, 0 winners, 150 conflicts
total winners across both instances: 1
independent DB check — hold_seats rows for target seat: 1 → PASS
```

The fair waitlist (the other half of the stretch goal — when a held seat is
released, waitlisted users get first claim in arrival order) is implemented
and covered by `tests/test_waitlist.py`, but isn't wired into the frontend UI
beyond a "join/leave waitlist" click on someone else's held seat.

## Project layout

```
backend/    FastAPI service — app/db/protocol.py is the concurrency core
db/         init.sql schema, loaded automatically by the Postgres container
web/        React + TypeScript seat-map UI (Vite)
```

## Time spent

~10 hours.
