import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.realtime.notify import manager

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/events/{event_id}/stream")
async def seat_updates_stream(websocket: WebSocket, event_id: uuid.UUID):
    key = str(event_id)
    await manager.connect(key, websocket)
    try:
        while True:
            # Clients don't send anything meaningful; this just detects disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(key, websocket)
        with contextlib.suppress(Exception):
            await websocket.close()
