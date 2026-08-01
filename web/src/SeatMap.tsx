import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { Booking, Hold, Seat } from "./types";
import { ApiError } from "./types";
import { useSeatStream } from "./useSeatStream";

const FALLBACK_POLL_MS = 15000;

interface Props {
  eventId: string;
}

export function SeatMap({ eventId }: Props) {
  const [seats, setSeats] = useState<Seat[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [waitlisted, setWaitlisted] = useState<Set<string>>(new Set());
  const [hold, setHold] = useState<Hold | null>(null);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [message, setMessage] = useState<string | null>(null);
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

  const grouped = useMemo(() => {
    const bySection = new Map<string, Map<string, Seat[]>>();
    for (const seat of seats) {
      if (!bySection.has(seat.section)) bySection.set(seat.section, new Map());
      const byRow = bySection.get(seat.section)!;
      if (!byRow.has(seat.row_label)) byRow.set(seat.row_label, []);
      byRow.get(seat.row_label)!.push(seat);
    }
    return bySection;
  }, [seats]);

  function toggleSelection(seatId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seatId)) next.delete(seatId);
      else next.add(seatId);
      return next;
    });
  }

  async function toggleWaitlist(seatId: string) {
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
        setMessage("You're on the waitlist — you'll get first claim if it frees up.");
      }
    } catch (e) {
      setMessage(describeError(e));
    }
  }

  function onSeatClick(seat: Seat) {
    if (booking) return;
    if (seat.status === "SOLD") return;
    if (hold?.seat_ids.includes(seat.id)) return;
    if (seat.status === "HELD") {
      toggleWaitlist(seat.id);
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

      <div className="sections">
        {[...grouped.entries()].map(([section, rows]) => (
          <div key={section} className="section">
            <h3>{section}</h3>
            {[...rows.entries()].map(([row, rowSeats]) => (
              <div key={row} className="row">
                <span className="row-label">{row}</span>
                <div className="row-seats">
                  {rowSeats.map((seat) => (
                    <button
                      key={seat.id}
                      className={`seat ${seatClass(seat, hold, selected, waitlisted)}`}
                      onClick={() => onSeatClick(seat)}
                      disabled={seat.status === "SOLD"}
                      title={seatTitle(seat, hold, waitlisted)}
                    >
                      {seat.seat_number}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ))}
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
      ? "Held by someone else — click to leave waitlist"
      : "Held by someone else — click to join waitlist";
  }
  return "Available";
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
