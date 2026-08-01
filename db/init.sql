-- Seat Reservation Service schema.
--
-- Loaded automatically by the official Postgres image on first container
-- start (docker-entrypoint-initdb.d convention). See DECISIONS.md for why
-- this is a plain SQL file rather than a migration framework.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

CREATE TYPE seat_status AS ENUM ('AVAILABLE', 'HELD', 'SOLD');
CREATE TYPE hold_status AS ENUM ('ACTIVE', 'EXPIRED', 'CONFIRMED', 'CANCELLED');

CREATE TABLE events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    venue       text NOT NULL,
    starts_at   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE holds (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id       uuid NOT NULL REFERENCES events(id),
    session_token  text NOT NULL,
    status         hold_status NOT NULL DEFAULT 'ACTIVE',
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL
);

CREATE INDEX idx_holds_session ON holds(session_token);

-- Seat status lives directly on the row: it's both the fast read path for
-- the seat map (no joins) and the exact row every mutation locks with
-- SELECT ... FOR UPDATE. See DECISIONS.md "Concurrency protocol".
CREATE TABLE seats (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id         uuid NOT NULL REFERENCES events(id),
    section          text NOT NULL,
    row_label        text NOT NULL,
    seat_number      int NOT NULL,
    status           seat_status NOT NULL DEFAULT 'AVAILABLE',
    hold_id          uuid REFERENCES holds(id),
    hold_expires_at  timestamptz,
    booking_id       uuid,
    UNIQUE (event_id, section, row_label, seat_number)
);

CREATE INDEX idx_seats_event ON seats(event_id);
CREATE INDEX idx_seats_hold ON seats(hold_id) WHERE hold_id IS NOT NULL;
-- Sweeper scans exactly this set every cycle.
CREATE INDEX idx_seats_expiring ON seats(hold_expires_at) WHERE status = 'HELD';

CREATE TABLE hold_seats (
    hold_id  uuid NOT NULL REFERENCES holds(id),
    seat_id  uuid NOT NULL REFERENCES seats(id),
    PRIMARY KEY (hold_id, seat_id)
);

CREATE TABLE bookings (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id           uuid NOT NULL REFERENCES events(id),
    hold_id            uuid NOT NULL REFERENCES holds(id),
    confirmation_code  text NOT NULL UNIQUE,
    customer_name      text NOT NULL,
    customer_email     text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE seats ADD CONSTRAINT fk_seats_booking
    FOREIGN KEY (booking_id) REFERENCES bookings(id);

-- Stretch goal: fair waitlist. Partitioned by seat_id so promotion for one
-- seat never contends with another seat's waitlist rows.
CREATE TABLE waitlist (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seat_id        uuid NOT NULL REFERENCES seats(id),
    session_token  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (seat_id, session_token)
);

CREATE INDEX idx_waitlist_seat ON waitlist(seat_id, created_at);
