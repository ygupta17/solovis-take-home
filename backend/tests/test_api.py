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
    resp = await client.get(
        f"/events/{event_id}/seats", headers={"X-Session-Token": "session-1"}
    )
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

    seats_resp = await client.get(f"/events/{event_id}/seats", headers=headers)
    assert seats_resp.json()[0]["status"] == "HELD"

    confirm_resp = await client.post(
        f"/holds/{hold_id}/confirm",
        json={"customer_name": "Ada", "customer_email": "ada@example.com"},
        headers=headers,
    )
    assert confirm_resp.status_code == 200
    assert "confirmation_code" in confirm_resp.json()

    seats_resp = await client.get(f"/events/{event_id}/seats", headers=headers)
    assert seats_resp.json()[0]["status"] == "SOLD"


async def test_confirm_rejects_invalid_characters_in_name(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "session-1"}
    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )
    hold_id = hold_resp.json()["id"]

    resp = await client.post(
        f"/holds/{hold_id}/confirm",
        json={"customer_name": "Ada<script>", "customer_email": "ada@example.com"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_input"

    # the hold survives a validation failure — it's still confirmable
    seats_resp = await client.get(f"/events/{event_id}/seats", headers=headers)
    assert seats_resp.json()[0]["status"] == "HELD"


async def test_confirm_accepts_real_world_name_punctuation(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "session-1"}
    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )
    hold_id = hold_resp.json()["id"]

    resp = await client.post(
        f"/holds/{hold_id}/confirm",
        json={"customer_name": "Mary-Jane O'Brien-García", "customer_email": "mj@example.com"},
        headers=headers,
    )
    assert resp.status_code == 200


async def test_confirm_rejects_malformed_email(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "session-1"}
    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )
    hold_id = hold_resp.json()["id"]

    resp = await client.post(
        f"/holds/{hold_id}/confirm",
        json={"customer_name": "Ada", "customer_email": "not-an-email"},
        headers=headers,
    )
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"] == "invalid_input"
    # Must be our own friendly wording, not email-validator's internal
    # message ("value is not a valid email address: ...") leaking through
    # to the user verbatim.
    assert body["detail"] == "Please enter a valid email address (e.g. name@example.com)."


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


async def test_join_and_leave_waitlist_return_valid_json_bodies(client, event_id, make_seat):
    """Regression test: join_waitlist previously returned a bare 201 with an
    empty body, which broke the frontend's fetch wrapper (it unconditionally
    tried to JSON-parse the response). Only caught by testing through the
    actual HTTP layer — protocol-level tests call the Python function
    directly and never touch response serialization at all.
    """
    seat_id = await make_seat()
    await client.post(
        f"/events/{event_id}/holds",
        json={"seat_ids": [str(seat_id)]},
        headers={"X-Session-Token": "holder"},
    )

    join_resp = await client.post(
        f"/seats/{seat_id}/waitlist", headers={"X-Session-Token": "waiter"}
    )
    assert join_resp.status_code == 201
    assert join_resp.json() == {"seat_id": str(seat_id), "waitlisted": True}

    leave_resp = await client.delete(
        f"/seats/{seat_id}/waitlist", headers={"X-Session-Token": "waiter"}
    )
    assert leave_resp.status_code == 204
    assert leave_resp.content == b""


async def test_join_waitlist_for_own_hold_is_rejected(client, event_id, make_seat):
    seat_id = await make_seat()
    headers = {"X-Session-Token": "holder"}
    await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=headers
    )

    resp = await client.post(f"/seats/{seat_id}/waitlist", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "own_hold"


async def test_seat_map_reveals_hold_id_only_to_the_promoted_waiter(client, event_id, make_seat):
    """A waitlist promotion creates a hold server-side — the promoted
    client never calls create_hold itself, so a plain seat-map refetch is
    the only way it finds out. seats.hold_id must come back non-null for
    the newly-promoted session and null for everyone else, even though the
    seat's `status` looks identical (HELD) to both.
    """
    seat_id = await make_seat()
    original = {"X-Session-Token": "original-holder"}
    waiter = {"X-Session-Token": "first-waiter"}
    bystander = {"X-Session-Token": "bystander"}

    hold_resp = await client.post(
        f"/events/{event_id}/holds", json={"seat_ids": [str(seat_id)]}, headers=original
    )
    hold_id = hold_resp.json()["id"]
    await client.post(f"/seats/{seat_id}/waitlist", headers=waiter)

    # Before release: nobody's session owns the (still original) hold.
    seats_resp = await client.get(f"/events/{event_id}/seats", headers=waiter)
    assert seats_resp.json()[0]["hold_id"] is None

    await client.delete(f"/holds/{hold_id}", headers=original)

    waiter_view = await client.get(f"/events/{event_id}/seats", headers=waiter)
    bystander_view = await client.get(f"/events/{event_id}/seats", headers=bystander)
    assert waiter_view.json()[0]["status"] == "HELD"
    assert waiter_view.json()[0]["hold_id"] is not None
    assert bystander_view.json()[0]["status"] == "HELD"
    assert bystander_view.json()[0]["hold_id"] is None
