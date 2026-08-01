import { getSessionToken } from "./session";
import type { ApiErrorBody, Booking, EventSummary, Hold, Seat } from "./types";
import { ApiError } from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function wsBaseUrl(): string {
  return BASE_URL.replace(/^http/, "ws");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Session-Token": getSessionToken(),
      ...init?.headers,
    },
  });
  if (!resp.ok) {
    let body: ApiErrorBody | null = null;
    try {
      const parsed = await resp.json();
      body = parsed.detail ?? parsed;
    } catch {
      body = null;
    }
    throw new ApiError(resp.status, body);
  }
  // Don't key off status code for "has a body" — 201s can be empty too
  // (POST /seats/{id}/waitlist is exactly that case). Read as text first
  // and only parse if there's actually something there, so any endpoint
  // that returns an empty success body doesn't throw on JSON.parse("").
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  listEvents: () => request<EventSummary[]>("/events"),
  getEvent: (eventId: string) => request<EventSummary>(`/events/${eventId}`),
  getSeats: (eventId: string) => request<Seat[]>(`/events/${eventId}/seats`),
  createHold: (eventId: string, seatIds: string[]) =>
    request<Hold>(`/events/${eventId}/holds`, {
      method: "POST",
      body: JSON.stringify({ seat_ids: seatIds }),
    }),
  confirmHold: (holdId: string, customerName: string, customerEmail: string) =>
    request<Booking>(`/holds/${holdId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ customer_name: customerName, customer_email: customerEmail }),
    }),
  cancelHold: (holdId: string) => request<void>(`/holds/${holdId}`, { method: "DELETE" }),
  joinWaitlist: (seatId: string) =>
    request<void>(`/seats/${seatId}/waitlist`, { method: "POST" }),
  leaveWaitlist: (seatId: string) =>
    request<void>(`/seats/${seatId}/waitlist`, { method: "DELETE" }),
};
