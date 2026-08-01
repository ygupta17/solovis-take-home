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
        {selected && (
          <p className="event-meta">
            {selected.name} — {selected.venue} —{" "}
            {new Date(selected.starts_at).toLocaleDateString()}
          </p>
        )}
      </header>

      {error && <div className="banner error">{error}</div>}

      {!selected && events.length > 1 && (
        <ul className="event-list">
          {events.map((e) => (
            <li key={e.id}>
              <button onClick={() => setSelectedId(e.id)}>
                {e.name} — {e.venue}
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && <SeatMap eventId={selected.id} />}
    </div>
  );
}
