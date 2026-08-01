import { useEffect, useState } from "react";
import { api } from "./api";
import { SeatMap } from "./SeatMap";
import type { EventSummary } from "./types";

export function App() {
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listEvents()
      .then((list) => {
        setEvents(list);
        if (list.length === 1) setSelectedId(list[0].id);
      })
      .catch(() => setError("Couldn't reach the API. Is the backend running?"));
  }, []);

  const selected = events.find((e) => e.id === selectedId) ?? null;

  return (
    <div className="app">
      <header>
        <h1>Seat Reservation</h1>
        {selected ? (
          <p className="event-meta">
            {selected.name} — {selected.venue} —{" "}
            {new Date(selected.starts_at).toLocaleDateString()}
            {events.length > 1 && (
              <>
                {" · "}
                <button className="link-button" onClick={() => setSelectedId(null)}>
                  all events
                </button>
              </>
            )}
          </p>
        ) : (
          <p className="event-meta">Pick an event to see its live seat map.</p>
        )}
      </header>

      {error && <div className="banner error">{error}</div>}

      {!selected && events.length > 0 && (
        <div className="event-grid">
          {events.map((e) => (
            <button key={e.id} className="event-card" onClick={() => setSelectedId(e.id)}>
              <span className={`event-card-layout ${e.layout}`}>{e.layout}</span>
              <strong>{e.name}</strong>
              <span className="event-card-venue">{e.venue}</span>
              <span className="event-card-date">
                {new Date(e.starts_at).toLocaleDateString(undefined, {
                  weekday: "short",
                  month: "short",
                  day: "numeric",
                })}
              </span>
            </button>
          ))}
        </div>
      )}

      {selected && <SeatMap eventId={selected.id} layout={selected.layout} />}
    </div>
  );
}
