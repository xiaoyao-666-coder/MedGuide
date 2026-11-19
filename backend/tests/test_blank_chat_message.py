"""Issue #43: a whitespace-only chat message must be rejected, not answered.

A message of only spaces/tabs/newlines counted toward ``min_length=1``
and came back 200 with a full generic answer, and the blank turn was
recorded in the session history. The feedback comment path already
rejects blank comments after strip(); the chat input now does the same.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import AppServices, app


@pytest.fixture()
def services(tmp_path, monkeypatch):
    """A fresh, isolated AppServices (fresh store + fresh auth DB)."""
    monkeypatch.setenv("MEDGUIDE_AUTH_DB_PATH", str(tmp_path / "auth.sqlite3"))
    current = AppServices()
    monkeypatch.setattr(main_module, "get_services", lambda: current)
    yield current
    current.close()


@pytest.fixture()
def client(services):
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def _register(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Correct-Horse-42"},
    )
    assert response.status_code == 201


def test_whitespace_only_message_is_rejected_and_not_recorded(client: TestClient) -> None:
    _register(client, "blank_spaces")
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.post("/api/chat", json={"session_id": session_id, "message": "   "})
    assert response.status_code == 422

    state = client.get(f"/api/sessions/{session_id}").json()
    assert state["turn_count"] == 0
    assert state["messages"] == []


def test_mixed_whitespace_message_is_rejected(client: TestClient) -> None:
    _register(client, "blank_mixed")
    session_id = client.post("/api/sessions").json()["session_id"]
    for blank in ("\t \n  \t", "\n\n", "\u3000\u3000"):
        response = client.post("/api/chat", json={"session_id": session_id, "message": blank})
        assert response.status_code == 422, blank

    state = client.get(f"/api/sessions/{session_id}").json()
    assert state["turn_count"] == 0


def test_normal_message_still_works(client: TestClient) -> None:
    _register(client, "blank_normal")
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        "/api/chat", json={"session_id": session_id, "message": "我咳嗽三天"}
    )
    assert response.status_code == 200
    assert response.json()["response_id"]


def test_message_with_surrounding_whitespace_still_works(client: TestClient) -> None:
    _register(client, "blank_padded")
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        "/api/chat", json={"session_id": session_id, "message": "  我咳嗽三天  "}
    )
    assert response.status_code == 200
    state = client.get(f"/api/sessions/{session_id}").json()
    assert state["turn_count"] == 1


def test_blank_message_rejected_on_stream_too(client: TestClient) -> None:
    _register(client, "blank_stream")
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        "/api/chat?stream=true", json={"session_id": session_id, "message": "   "}
    )
    assert response.status_code == 422
