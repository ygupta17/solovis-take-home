# Decisions


## Architecture

Stack: Postgres, FastAPI + Python + asyncpg backend, React/TypeScript + Vite frontend,
Docker Compose for local orchestration.


**Why this stack:** Postgres has a built-in way to lock a single row so only one request can touch it at a time, and it hands that lock out in the order requests show up, which fits the requirement of "never sell a seat twice" and "be fair about who gets it" both need. That meant not having to build a custom locking system or bring in a caching technology like Redis just for this one problem.

FastAPI and asyncpg (the Python web framework and database driver) were 
a good fit on top of Postgres since they let the application handle people
grabbing seas at the same seat at once without getting stuck waiting on any one of them, and they let the code talk to Postgres directly in a way that keeps the locking logic easy to see and follow rather
than hiding it behind a layer that does things automatically. 

React with TypeScript on the frontend mainly helps keep track of the different states a seat can be in — open, held by someone, sold and Vite makes the dev experience fast. Docker Compose ties it all together so anyone can start the whole app with one command instead of installing Postgres and everything else by hand.

Docker Compose is what makes the whole stack (Postgres, API, frontend)
start with a single command and behave the same on any machine — no one
has to install Postgres locally, match a Python version, or hand-wire the
three pieces together.

## Other architectural choices/features

- **Looking up seats doesn't use any locking.** It's a plain read, so
  checking the seat map never has to wait behind someone else's
  in-progress hold or purchase. It might show info that's a split-second
  out of date, but that's fine — actually holding or buying a seat always
  double-checks the real state at that moment anyway. It does now include
  one `LEFT JOIN` to `holds`, added so a client who gets promoted off a
  waitlist can discover "that seat is actually mine now" from a normal
  refetch — still unlocked and still fast, just not literally a
  single-table scan anymore.
- **Live updates go through Postgres itself (`LISTEN`/`NOTIFY`)** instead
  of the app tracking connected clients in memory. Whenever a seat changes,
  Postgres pings every server instance, which pings connected browsers to
  say "something changed, go re-fetch" — not "here's exactly what changed,"
  since that signal isn't guaranteed to arrive. Simpler and safer than
  trying to keep everyone's view perfectly in sync via patches that could
  get lost. This means *every* connected
  client re-fetches the full seat list on *any* change to that event, not
  just the seats that changed — fine at this app's scale, but a genuinely
  popular event with thousands of live viewers would turn one hold into
  thousands of full-list queries.
- **Validation logic and error handling.** Not allowing invalid characters in the name and making sure email has a valid domain(by ensuring there is a period in the email) at time of confirming a seat


## What I deliberately left out, and why

- **Cloud IaC.** The application is fully containerized (Dockerfiles + Compose), which is useful for Terraform/a cloud provider, but I didn't
spend the remaining time actually standing it up in the cloud. In a production environment I would have hosted this app on the cloud.
- **Payments/Refunds.** "Confirm booking" is the final stage in the end to end flow; no real payment. Similarly, there are no refunds or seat returns in this. In a production application, the use of software like Stripe would have been the go to choice to robustly handle payment.
- **Confirmation emails** Do not have any messages sent to the email included after confirmation but in a production app I would have included that as most ticketing systems provide some sort of confirmation via text message or email. 
- **Frontend test suite.** Verified manually rather than with automated component/e2e tests. Testing for concurrency is where most of the logic and risk is and testing for that is covered in the backend.
- **More polished UI.** For a production application, I would have had a more polished UI and try to mimic some UI styles from common ticketing websites like Ticketmaster or Stubhub. 
- **User authentication/authorization.** Identity here is just a random ID
  the browser generates and stores itself — no login, no accounts. A production version of this app would have two realistic options: a managed provider like Auth0, Clerk, or AWS Cognito  or rolling it in-house with FastAPI's own OAuth2/JWT support. Two consequences worth being explicit about: nothing verifies that self-asserted ID (a client can send any value in the header), and there's no rate limiting on holds/waitlist joins — both would need real accounts to fix properly, so they're the same cut, not separate gaps.
- **CORS is wide open.** (`allow_origins=["*"]` in `app/main.py`). Fine for a
  take-home hitting `localhost`, not something to carry past it — a real
  deploy would lock this down to the actual frontend origin per environment.

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
    ~0.38s wall-clock, DB confirms exactly 1 `hold_seats` row.
  - 500 concurrent requests, single instance → 1 winner, ~3.67s wall-clock
    (right at the `lock_timeout` boundary, confirming it's load-bearing).
  - **300 concurrent requests split across two independent API containers
    sharing one Postgres** (the multi-instance stretch) → 1 winner total, DB
    confirms exactly 1 row. Reproduce: `docker compose up -d`, then `docker
    run` a second `api` image on port 8001 pointed at the same `db` (exact
    command in README), then run the script with `CONCURRENCY=300`.

