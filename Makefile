.PHONY: up down test lint load-test seed

# Full local stack (Postgres + API + web), one command.
up:
	docker compose up --build

down:
	docker compose down

# Runs the backend test suite (incl. the concurrency proof) against the
# disposable db-test instance. Brings db-test up if it isn't already.
test:
	docker compose up -d db-test
	cd backend && \
		( [ -d .venv ] || python3 -m venv .venv ) && \
		. .venv/bin/activate && \
		pip install -q -r requirements-dev.txt && \
		TEST_DATABASE_URL=postgresql://seats:seats@localhost:5433/seats_test python -m pytest -v

lint:
	cd backend && . .venv/bin/activate && ruff check app tests
	cd web && npm run lint && npm run typecheck

# Standalone evidence script: hammers one seat with concurrent clients
# against the real running API and prints the outcome. Requires `make up`
# (or at least the api service) to be running first.
load-test:
	cd backend && . .venv/bin/activate && python scripts/load_test.py

seed:
	docker compose exec api python -m app.seed
