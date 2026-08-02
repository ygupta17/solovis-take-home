import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { computeVenueLayout, sectionBandPath, SEAT_BOX } from "./stadiumLayout";
import type { Booking, Hold, Seat, VenueLayoutKind } from "./types";
import { ApiError } from "./types";
import { useSeatStream } from "./useSeatStream";

const FALLBACK_POLL_MS = 15000;

// One tint per section, cycled by section order — ties each section's arc
// band to its label so it's visually obvious where one section ends and
// the next begins, instead of relying on the text label alone.
const SECTION_COLORS = [
  { fill: "#dbeafe", stroke: "#93c5fd", text: "#1e40af" },
  { fill: "#fce7f3", stroke: "#f9a8d4", text: "#9d174d" },
  { fill: "#dcfce7", stroke: "#86efac", text: "#166534" },
  { fill: "#fef3c7", stroke: "#fcd34d", text: "#92400e" },
  { fill: "#ede9fe", stroke: "#c4b5fd", text: "#5b21b6" },
  { fill: "#e0f2fe", stroke: "#7dd3fc", text: "#075985" },
];

interface Props {
  eventId: string;
  layout: VenueLayoutKind;
}

export function SeatMap({ eventId, layout }: Props) {
  const [seats, setSeats] = useState<Seat[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [waitlisted, setWaitlisted] = useState<Set<string>>(new Set());
  const [hold, setHold] = useState<Hold | null>(null);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [detailSeatId, setDetailSeatId] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  const holdRef = useRef(hold);
  holdRef.current = hold;

  const refreshSeats = useCallback(async () => {
    try {
      setSeats(await api.getSeats(eventId));
    } catch {
      setMessage("Couldn't load the seat map. Retrying shortly...");
    }
  }, [eventId]);

  useEffect(() => {
    refreshSeats();
    const poll = setInterval(refreshSeats, FALLBACK_POLL_MS);
    return () => clearInterval(poll);
  }, [refreshSeats]);

  useSeatStream(eventId, refreshSeats);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const secondsRemaining = hold
    ? Math.max(0, Math.round((new Date(hold.expires_at).getTime() - now) / 1000))
    : null;

  useEffect(() => {
    if (hold && secondsRemaining === 0) {
      setHold(null);
      setMessage("Your hold expired — those seats are back up for grabs.");
      refreshSeats();
    }
  }, [hold, secondsRemaining, refreshSeats]);

  const detailSeat = seats.find((s) => s.id === detailSeatId) ?? null;

  useEffect(() => {
    // If the seat we're showing details for stopped being HELD (freed,
    // promoted, sold) while the panel was open, there's nothing left to
    // show — close it rather than leave stale actions on screen.
    if (detailSeatId && (!detailSeat || detailSeat.status !== "HELD")) {
      setDetailSeatId(null);
      setDetailError(null);
    }
  }, [detailSeatId, detailSeat]);

  const venue = useMemo(() => computeVenueLayout(seats, layout), [seats, layout]);

  function toggleSelection(seatId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seatId)) next.delete(seatId);
      else next.add(seatId);
      return next;
    });
  }

  async function toggleWaitlist(seatId: string) {
    setDetailError(null);
    try {
      if (waitlisted.has(seatId)) {
        await api.leaveWaitlist(seatId);
        setWaitlisted((prev) => {
          const next = new Set(prev);
          next.delete(seatId);
          return next;
        });
      } else {
        await api.joinWaitlist(seatId);
        setWaitlisted((prev) => new Set(prev).add(seatId));
      }
    } catch (e) {
      // Shown inline in the detail panel, not the page-level banner — this
      // is a direct response to the button the user just clicked, not a
      // general status update.
      setDetailError(describeError(e));
    }
  }

  function onSeatClick(seat: Seat) {
    if (booking) return;
    if (seat.status === "SOLD") return;
    if (hold?.seat_ids.includes(seat.id)) return;
    if (seat.status === "HELD") {
      // Don't act on the click itself — show what's going on and let the
      // user explicitly opt into the waitlist rather than silently
      // enrolling them. See DECISIONS.md.
      setDetailError(null);
      setDetailSeatId(seat.id);
      return;
    }
    if (hold) return; // resolve the current hold before selecting more
    toggleSelection(seat.id);
  }

  async function onHoldSelected() {
    if (selected.size === 0) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.createHold(eventId, [...selected]);
      setHold(result);
      setSelected(new Set());
    } catch (e) {
      setMessage(describeError(e));
      if (e instanceof ApiError && e.body?.seat_ids) {
        const unavailable = new Set(e.body.seat_ids);
        setSelected((prev) => new Set([...prev].filter((id) => !unavailable.has(id))));
      }
      refreshSeats();
    } finally {
      setBusy(false);
    }
  }

  async function onCancelHold() {
    if (!hold) return;
    setBusy(true);
    try {
      await api.cancelHold(hold.id);
      setHold(null);
      refreshSeats();
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm(e: React.FormEvent) {
    e.preventDefault();
    if (!hold) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.confirmHold(hold.id, name, email);
      setBooking(result);
      setHold(null);
    } catch (e) {
      setMessage(describeError(e));
      setHold(null);
      refreshSeats();
    } finally {
      setBusy(false);
    }
  }

  function startOver() {
    setBooking(null);
    setSelected(new Set());
    setName("");
    setEmail("");
    setMessage(null);
  }

  return (
    <div className="seat-map">
      <Legend />
      {message && (
        <div className="banner" role="status">
          {message}
        </div>
      )}

      {detailSeat && (
        <div className="banner detail-panel" role="status">
          <span>
            <strong>{seatLabel(detailSeat)}</strong> is currently held by someone else.
            {waitlisted.has(detailSeat.id) && " You're on the waitlist for it."}
          </span>
          {detailError && <span className="detail-error">{detailError}</span>}
          <span className="detail-actions">
            {waitlisted.has(detailSeat.id) ? (
              <button onClick={() => toggleWaitlist(detailSeat.id)}>Leave waitlist</button>
            ) : (
              <button onClick={() => toggleWaitlist(detailSeat.id)}>Join waitlist</button>
            )}
            <button
              className="secondary"
              onClick={() => {
                setDetailSeatId(null);
                setDetailError(null);
              }}
            >
              Close
            </button>
          </span>
        </div>
      )}

      <div className="venue-scroll">
        <div className="venue" style={{ width: venue.width, height: venue.height }}>
          <svg
            className="venue-bands"
            width={venue.width}
            height={venue.height}
            aria-hidden="true"
          >
            <g transform={`translate(${venue.centerX}, ${venue.centerY})`}>
              {venue.sections.map((section, i) => {
                const color = SECTION_COLORS[i % SECTION_COLORS.length];
                return (
                  <path
                    key={section.name}
                    d={sectionBandPath(section)}
                    fill={color.fill}
                    stroke={color.stroke}
                    strokeWidth={1}
                  />
                );
              })}
            </g>
          </svg>

          <div
            className={`venue-center ${layout}`}
            style={
              layout === "stadium"
                ? {
                    left: venue.centerX,
                    top: venue.centerY,
                    width: venue.centerRadius * 2,
                    height: venue.centerRadius * 2,
                  }
                : { left: venue.centerX, top: venue.centerY }
            }
          >
            {venue.centerLabel}
          </div>

          {venue.sections.map((section, i) => {
            const color = SECTION_COLORS[i % SECTION_COLORS.length];
            return (
            <div key={section.name}>
              <span
                className="section-label"
                style={{
                  left: venue.centerX + section.labelX,
                  top: venue.centerY + section.labelY,
                  background: color.fill,
                  borderColor: color.stroke,
                  color: color.text,
                }}
              >
                {section.name}
              </span>
              {section.seats.map(({ seat, x, y }) => (
                <button
                  key={seat.id}
                  className={`seat ${seatClass(seat, hold, selected, waitlisted)}`}
                  style={{
                    left: venue.centerX + x - SEAT_BOX / 2,
                    top: venue.centerY + y - SEAT_BOX / 2,
                    width: SEAT_BOX,
                    height: SEAT_BOX,
                  }}
                  onClick={() => onSeatClick(seat)}
                  disabled={seat.status === "SOLD"}
                  title={`${seatLabel(seat)} — ${seatTitle(seat, hold, waitlisted)}`}
                >
                  {seat.seat_number}
                </button>
              ))}
            </div>
            );
          })}
        </div>
      </div>

      {!hold && !booking && selected.size > 0 && (
        <div className="checkout-bar">
          <span>{selected.size} seat(s) selected</span>
          <button disabled={busy} onClick={onHoldSelected}>
            Hold seats
          </button>
        </div>
      )}

      {hold && (
        <div className="checkout-bar hold-panel">
          <div>
            Holding {hold.seat_ids.length} seat(s) — expires in{" "}
            <strong>{secondsRemaining}s</strong>
          </div>
          <form onSubmit={onConfirm}>
            <input
              placeholder="Full name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              placeholder="Email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <button type="submit" disabled={busy}>
              Confirm booking
            </button>
            <button type="button" disabled={busy} onClick={onCancelHold} className="secondary">
              Release seats
            </button>
          </form>
        </div>
      )}

      {booking && (
        <div className="checkout-bar confirmation">
          <div>
            Booked! Confirmation code: <strong>{booking.confirmation_code}</strong>
          </div>
          <button onClick={startOver}>Book more seats</button>
        </div>
      )}
    </div>
  );
}

function seatClass(
  seat: Seat,
  hold: Hold | null,
  selected: Set<string>,
  waitlisted: Set<string>,
): string {
  if (seat.status === "SOLD") return "sold";
  if (hold?.seat_ids.includes(seat.id)) return "mine";
  if (seat.status === "HELD") return waitlisted.has(seat.id) ? "held waitlisted" : "held";
  return selected.has(seat.id) ? "selected" : "available";
}

function seatTitle(seat: Seat, hold: Hold | null, waitlisted: Set<string>): string {
  if (seat.status === "SOLD") return "Sold";
  if (hold?.seat_ids.includes(seat.id)) return "Held by you";
  if (seat.status === "HELD") {
    return waitlisted.has(seat.id)
      ? "Held by someone else — you're on the waitlist"
      : "Held by someone else — click for details";
  }
  return "Available";
}

function seatLabel(seat: Seat): string {
  return `${seat.section} ${seat.row_label}${seat.seat_number}`;
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.body?.error) {
      case "seat_unavailable":
        return "Someone grabbed one of those seats first. Try another.";
      case "seat_contested":
        return "That seat is under heavy demand right now — try again in a moment.";
      case "hold_not_active":
        return e.body.status === "EXPIRED"
          ? "Your hold expired before you confirmed."
          : "That hold is no longer active.";
      case "already_on_waitlist":
        return "You're already on the waitlist for that seat.";
      case "seat_available":
        return "That seat just became available — select it directly.";
      case "own_hold":
        return "You already hold this seat (maybe in another tab) — nothing to wait for.";
      default:
        return "Something went wrong. Please try again.";
    }
  }
  return "Something went wrong. Please try again.";
}

function Legend() {
  return (
    <div className="legend">
      <span>
        <i className="swatch available" /> Available
      </span>
      <span>
        <i className="swatch selected" /> Selected
      </span>
      <span>
        <i className="swatch mine" /> Your hold
      </span>
      <span>
        <i className="swatch held" /> Held by others
      </span>
      <span>
        <i className="swatch sold" /> Sold
      </span>
    </div>
  );
}
