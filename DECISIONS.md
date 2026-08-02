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

## Alternatives considered and/or rejected

- **For concurrency**, could have gone with a "version number + retry"
  approach instead of Postgres's row locking — check a version number,
  and if it changed, try again. Problem is that doesn't really wait in
  line for anything; under a rush it's just whoever's retry happens to
  land first, which isn't really "fair," just lucky timing. A separate
  "advisory lock" was another option, but there's already exactly one row
  per seat to lock, so that would've just been an extra thing to keep
  track of for nothing extra in return.
- **For live updates**, the app could keep its own list of who's connected
  and push updates to them directly, instead of going through Postgres's
  `LISTEN`/`NOTIFY`. That falls apart the moment there's more than one
  server running, since server A has no idea who's connected to server B.
  Also considered sending the *exact* details of what changed instead of
  just "something changed, go check" — but those messages can get lost in
  transit, and then someone's screen would be quietly wrong with no way to
  notice. "Just go re-check" fixes itself instead of staying wrong.
- **For handing a freed seat to the next waitlisted person**, there's a
  common trick called `SKIP LOCKED` — skip past anything busy and grab
  whatever's next available. That's built for pulling jobs off a shared
  queue where any of them will do. Here there's only one specific "next
  person in line" for a given seat, so skipping would mean permanently
  passing over them, not just making them wait a beat.
- **Skipped an ORM** (a tool that writes SQL for you) since the whole
  point of this project is the locking logic itself — burying it behind a
  tool that does things automatically would hide the one part worth
  looking at.
- **Used one plain SQL file for the schema** instead of a proper migration
  tool like Alembic. This means there's no track record to fall back on if the schema needs to
  change later.


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

