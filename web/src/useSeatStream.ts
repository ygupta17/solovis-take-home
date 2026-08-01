import { useEffect, useRef } from "react";
import { wsBaseUrl } from "./api";

const RECONNECT_DELAY_MS = 2000;

/**
 * Subscribes to the event's WebSocket stream and calls onInvalidate whenever
 * the server signals "something changed" — the client is expected to
 * refetch a fresh snapshot rather than trust any payload as an applied
 * delta (see backend app/realtime/notify.py for why). Also refetches on
 * (re)connect, since a dropped connection may have missed messages.
 */
export function useSeatStream(eventId: string, onInvalidate: () => void) {
  const onInvalidateRef = useRef(onInvalidate);
  onInvalidateRef.current = onInvalidate;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    function connect() {
      if (stopped) return;
      socket = new WebSocket(`${wsBaseUrl()}/ws/events/${eventId}/stream`);
      socket.onopen = () => onInvalidateRef.current();
      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "invalidate") onInvalidateRef.current();
        } catch {
          // ignore malformed frames
        }
      };
      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => socket?.close();
    }

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [eventId]);
}
