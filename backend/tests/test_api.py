"""HTTP-level tests: the API contract (status codes, JSON shapes, header
handling) on top of the protocol layer already covered exhaustively in
test_protocol.py/test_concurrency.py.
"""

import httpx
import pytest

from app.dependencies import get_pool
from app.main import app


@pytest.fixture
async def client(pool):
    app.dependency_overrides[get_pool] = lambda: pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_events(client, event_id):
    resp = await client.get("/events")
    assert resp.status_code == 200
    assert any(e["id"] == str(event_id) for e in resp.json())


async def test_seat_map(client, event_id, make_seat):
    seat_id = await make_seat()
    resp = await client.get(f"/events/{event_id}/seats")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(seat_id)
    assert body[0]["status"] == "AVAILABLE"


async def test_hold_requires_session_header(client, event_id, make_seat):
    seat_id = await make_seat()
    resp = await client.post(f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]})
    assert resp.status_code == 422


async def test_full_hold_confirm_flow(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "session-1"}

    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["id"]

    seats_resp = await client.get(f"/events/{event_id}/seats")
    assert seats_resp.json()[0]["status"] == "HELD"

    confirm_resp = await client.post(
        f"/holds/{hold_id}/confirm",
        json={"customer_name": "Ada", "customer_email": "ada@example.com"},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    assert "confirmation_code" in confirm_resp.json()

    seats_resp = await client.get(f"/events/{event_id}/seats")
    assert seats_resp.json()[0]["status"] == "SOLD"


async def test_contested_hold_returns_409(client, event_id, make_seat):
    seat_id = await make_seat()
    await client.post(
        f"/events/{event_id}/holds",
        json={"seat_ids": [str(seat_id)]},
        headers={"X-Session-Token": "session-1"},
    )
    resp = await client.post(
        f"/events/{event_id}/holds",
        json={"seat_ids": [str(seat_id)]},
        headers={"X-Session-Token": "session-2"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "seat_unavailable"


async def test_cancel_then_rehold(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "session-1"}
    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )
    hold_id = hold_resp.json()["id"]

    cancel_resp = await client.delete(f"/holds/{hold_id}", headers=headers)
    assert cancel_resp.status_code == 204

    resp = await client.post(
        f"/events/{event_id}/holds",
        json={"seat_ids": [str(seat_id)]},
        headers={"X-Session-Token": "session-2"},
    )
    assert resp.status_code == 201
