"""Issue #42: a long session must not silently drop its earliest turns.

On the fixed chain tip, after 105 turns GET /api/sessions/{id} reported
turn_count=105 but only the last 100 messages survived: the first 5
turns were gone for good. The cause was the ``messages[-100:]`` /
``response_ids[-100:]`` caps in workflow.py. The fix keeps the whole
transcript (and every response id, so early turns stay votable — the
F10 symptom disappears with it).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from conftest import login_client

TURNS = 105


def test_105_turns_keep_full_history() -> None:
    with TestClient(app, base_url="https://testserver") as client:
        login_client(client, "long_session_user")
        session_id = client.post("/api/sessions").json()["session_id"]

        first_response_id = None
        for turn in range(1, TURNS + 1):
            response = client.post(
                "/api/chat",
                json={"session_id": session_id, "message": f"第{turn}轮：头有点疼"},
            )
            assert response.status_code == 200, response.text
            if turn == 1:
                first_response_id = response.json()["response_id"]

        state = client.get(f"/api/sessions/{session_id}").json()
        assert state["turn_count"] == TURNS
        messages = state["messages"]
        # 105 turns = 105 user messages + 105 assistant replies, all kept.
        assert len(messages) == 2 * TURNS
        assert messages[0]["role"] == "user"
        assert "第1轮" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert messages[1].get("response_id") == first_response_id
        assert messages[-2]["role"] == "user"
        assert f"第{TURNS}轮" in messages[-2]["content"]

        # F10 side effect: the first turn is still votable, because its
        # response id was not truncated away.
        voted = client.post(
            "/api/feedback",
            json={"session_id": session_id, "message_id": first_response_id, "rating": "up"},
        )
        assert voted.status_code == 200, voted.text
        assert voted.json()["changed"] is True
