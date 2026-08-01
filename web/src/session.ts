const STORAGE_KEY = "seat-reservation-session-token";

/**
 * Opaque client-generated identity, persisted in localStorage. There is no
 * account system in this project (see DECISIONS.md) — this is just enough
 * identity for the server to know which browser tab owns which hold.
 */
export function getSessionToken(): string {
  let token = localStorage.getItem(STORAGE_KEY);
  if (!token) {
    token = crypto.randomUUID();
    localStorage.setItem(STORAGE_KEY, token);
  }
  return token;
}
