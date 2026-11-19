"""Issue #41: revoking or changing a vote must not leave its comment
behind as a live verdict.

On the fixed chain tip, DELETE /api/feedback removed only the rating:
the old comment kept reading back through GET /api/feedback/comments,
and changing the vote re-labeled the old criticism under the new rating
(a "down" comment showing up under an "up"). The fix clears the stored
comment on a revoke and on a rating change, and "commented" counts only
messages that currently carry a readable comment.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import AuthStore
from app.main import app
from app.metrics import MetricsStore
from app.store import SessionStore

PASSWORD = "Correct-Horse-42"
COMMENT_USER = "revoke_comment_user"


def _login(client) -> None:
    assert client.post(
        "/api/auth/register", json={"username": COMMENT_USER, "password": PASSWORD}
    ).status_code == 201


def _services(tmp_path):
    """Isolated offline services: fresh auth/store/metrics per test."""
    return SimpleNamespace(
        production=False,
        mode="offline",
        local=False,
        api_tokens=(),
        production_missing=(),
        auth=AuthStore(
            tmp_path / "auth.sqlite3",
            session_ttl_seconds=300,
            cookie_secure=False,
            password_iterations=100_000,
        ),
        store=SessionStore(mode="offline"),
        metrics=MetricsStore(),
    )


def _client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _seed_session(services, owner_id=None, response_ids=("resp-1", "resp-2")):
    state = services.store.create(owner_id=owner_id)
    state["response_ids"] = list(response_ids)
    return services.store.save(state)


def _post_feedback(client, session_id, message_id, rating, comment=None):
    payload = {"session_id": session_id, "message_id": message_id, "rating": rating}
    if comment is not None:
        payload["comment"] = comment
    return client.post("/api/feedback", json=payload)


def _revoke(client, session_id, message_id):
    return client.delete(
        "/api/feedback", params={"session_id": session_id, "message_id": message_id}
    )


def test_revoke_removes_comment_and_commented_count(tmp_path, monkeypatch) -> None:
    services = _services(tmp_path)
    monkeypatch.setattr(main_module, "get_services", lambda: services)
    state = _seed_session(services, owner_id=f"user:{COMMENT_USER}")
    session_id = state["session_id"]
    with _client() as client:
        _login(client)
        assert _post_feedback(client, session_id, "resp-1", "down", "回答太笼统了").json()["changed"] is True
        assert client.get("/api/feedback/comments").json()["count"] == 1
        assert services.metrics.snapshot()["feedback"].get("commented") == 1

        revoked = _revoke(client, session_id, "resp-1")
        assert revoked.status_code == 200
        assert revoked.json()["changed"] is True
        # The comment no longer reads back as a live verdict.
        assert client.get("/api/feedback/comments").json() == {"comments": [], "count": 0}
        assert "commented" not in services.metrics.snapshot()["feedback"]


def test_change_vote_clears_old_comment(tmp_path, monkeypatch) -> None:
    services = _services(tmp_path)
    monkeypatch.setattr(main_module, "get_services", lambda: services)
    state = _seed_session(services, owner_id=f"user:{COMMENT_USER}")
    session_id = state["session_id"]
    with _client() as client:
        _login(client)
        _post_feedback(client, session_id, "resp-1", "down", "回答太笼统了")
        changed = _post_feedback(client, session_id, "resp-1", "up")
        assert changed.json()["changed"] is True
        # The old criticism is gone, not re-labeled under the up vote.
        assert client.get("/api/feedback/comments").json() == {"comments": [], "count": 0}
        assert "commented" not in services.metrics.snapshot()["feedback"]


def test_revoke_leaves_other_messages_comments_alone(tmp_path, monkeypatch) -> None:
    services = _services(tmp_path)
    monkeypatch.setattr(main_module, "get_services", lambda: services)
    state = _seed_session(services, owner_id=f"user:{COMMENT_USER}")
    session_id = state["session_id"]
    with _client() as client:
        _login(client)
        _post_feedback(client, session_id, "resp-1", "down", "回答太笼统了")
        _post_feedback(client, session_id, "resp-2", "up", "这个回答很清楚")
        assert _revoke(client, session_id, "resp-1").json()["changed"] is True
        listed = client.get("/api/feedback/comments").json()
        assert listed["count"] == 1
        assert listed["comments"][0]["message_id"] == "resp-2"
        assert listed["comments"][0]["comment"] == "这个回答很清楚"
        assert services.metrics.snapshot()["feedback"].get("commented") == 1


def test_vote_again_after_revoke_with_fresh_comment(tmp_path, monkeypatch) -> None:
    services = _services(tmp_path)
    monkeypatch.setattr(main_module, "get_services", lambda: services)
    state = _seed_session(services, owner_id=f"user:{COMMENT_USER}")
    session_id = state["session_id"]
    with _client() as client:
        _login(client)
        _post_feedback(client, session_id, "resp-1", "down", "回答太笼统了")
        _revoke(client, session_id, "resp-1")
        again = _post_feedback(client, session_id, "resp-1", "up", "改后觉得有帮助")
        assert again.json()["changed"] is True
        listed = client.get("/api/feedback/comments").json()
        assert listed["count"] == 1
        assert listed["comments"][0] == {
            "session_id": session_id,
            "message_id": "resp-1",
            "rating": "up",
            "comment": "改后觉得有帮助",
            "seq": 2,
        }
        assert services.metrics.snapshot()["feedback"].get("commented") == 1
