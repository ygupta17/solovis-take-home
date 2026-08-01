"""Fan-out for live seat-map updates, built on Postgres LISTEN/NOTIFY.

Every app instance runs its own listener task on a dedicated (non-pooled)
connection. `protocol.py` calls `pg_notify('seat_updates', event_id)` inside
the same transaction as each committed mutation, so this reuses Postgres as
the single source of truth for propagation too — instance count doesn't
matter, every instance sees every commit.

Deliberately NOT trusted as an ordered, lossless delta stream: NOTIFY has no
durability or redelivery guarantee, and a dropped/reconnecting LISTEN
connection silently loses whatever fired during the gap. So every notify
(and every reconnect) is treated as nothing more than an invalidation hint —
"something changed for this event" — and connected WebSocket clients are
told to refetch the REST snapshot rather than being sent a payload to apply
as a trusted diff. See DECISIONS.md.
"""

import asyncio
import contextlib
import logging
from collections import defaultdict

import asyncpg
from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_SECONDS = 2


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, event_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._clients[event_id].add(ws)

    def disconnect(self, event_id: str, ws: WebSocket) -> None:
        self._clients[event_id].discard(ws)
        if not self._clients[event_id]:
            self._clients.pop(event_id, None)

    async def invalidate(self, event_id: str) -> None:
        await self._broadcast(self._clients.get(event_id, set()))

    async def invalidate_all(self) -> None:
        """Used after a reconnect, when we can't tell what we might have
        missed while disconnected."""
        for clients in list(self._clients.values()):
            await self._broadcast(clients)

    @staticmethod
    async def _broadcast(clients: set[WebSocket]) -> None:
        for ws in list(clients):
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "invalidate"})


manager = ConnectionManager()


async def run_listener(stop_event: asyncio.Event) -> None:
    """Long-running task: (re)connects and LISTENs until stop_event is set."""
    while not stop_event.is_set():
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(settings.database_url)
            await conn.add_listener("seat_updates", _on_notify)
            logger.info("listening on seat_updates")
            await manager.invalidate_all()  # covers whatever happened before we connected
            while not stop_event.is_set():
                # add_listener delivers via asyncpg's background reader; we
                # just need to keep this connection alive and idle here.
                await asyncio.sleep(1)
                if conn.is_closed():
                    raise ConnectionError("listen connection closed")
        except (OSError, asyncpg.PostgresError, ConnectionError) as exc:
            logger.warning("seat_updates listener dropped (%s), reconnecting", exc)
        finally:
            if conn is not None and not conn.is_closed():
                await conn.close()
        if not stop_event.is_set():
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)


def _on_notify(_conn, _pid, _channel, payload: str) -> None:
    asyncio.create_task(manager.invalidate(payload))
