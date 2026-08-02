export type SeatStatus = "AVAILABLE" | "HELD" | "SOLD";
export type VenueLayoutKind = "theater" | "stadium";

export interface EventSummary {
  id: string;
  name: string;
  venue: string;
  starts_at: string;
  layout: VenueLayoutKind;
}

export interface Seat {
  id: string;
  section: string;
  row_label: string;
  seat_number: number;
  status: SeatStatus;
  hold_expires_at: string | null;
  // Only set when the current session's own hold owns this seat — null for
  // every other HELD seat, including other people's. Lets the client notice
  // a waitlist promotion (a hold created server-side, not by this browser
  // calling createHold) from a plain seat-map refetch.
  hold_id: string | null;
}

export interface Hold {
  id: string;
  event_id: string;
  seat_ids: string[];
  expires_at: string;
}

export interface Booking {
  id: string;
  confirmation_code: string;
  seat_ids: string[];
}

export interface ApiErrorBody {
  error: string;
  detail?: string;
  seat_ids?: string[];
  status?: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ApiErrorBody | null,
  ) {
    super(body?.error ?? `request failed with status ${status}`);
  }
}
