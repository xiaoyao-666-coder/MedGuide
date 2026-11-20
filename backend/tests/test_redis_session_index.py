"""Issue #45: per-owner session index for the Redis session adapter.

Without the index, ``SessionStore.list_sessions`` returned ``[]`` on a
Redis backend and the UI only rendered a placeholder for the current
session, which looked like "开启新对话" swallowed older sessions.
"""
from __future__ import annotations

import pytest

from app.infra import RedisSessionAdapter
from app.store import SessionBackendError, SessionStore


class _FakePipeline:
    """Minimal pipeline double that applies queued writes on execute()."""

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self.queued: list[tuple] = []
        self.watch_calls: list[str] = []
        self.execute_calls = 0
        self.reset_calls = 0

    def watch(self, key):
        self.watch_calls.append(key)

    def get(self, key):
        return self._redis.values.get(key)

    def multi(self):
        return None

    def set(self, key, value, **kwargs):
        self.queued.append(("set", key, value, kwargs))

    def zadd(self, key, mapping, nx=False, **kwargs):
        self.queued.append(("zadd", key, dict(mapping), nx))

    def expire(self, key, ttl):
        self.queued.append(("expire", key, ttl))

    def execute(self):
        self.execute_calls += 1
        for op in self.queued:
            if op[0] == "set":
                self._redis.values[op[1]] = op[2]
                if "ex" in op[3]:
                    self._redis.ttls[op[1]] = op[3]["ex"]
            elif op[0] == "zadd":
                zset = self._redis.zsets.setdefault(op[1], {})
                for member, score in op[2].items():
                    if op[3] and member in zset:
                        continue
                    zset[member] = score
            elif op[0] == "expire":
                self._redis.ttls[op[1]] = op[2]
        self.queued.clear()

    def reset(self):
        self.reset_calls += 1
        self.queued.clear()


class _FakeRedis:
    """In-memory Redis double with sets and TTL bookkeeping."""

    def __init__(self, *, with_pipeline: bool = True) -> None:
        self.values: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}
        self._pipeline = _FakePipeline(self)
        if with_pipeline:
            self.pipeline = lambda: self._pipeline

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **kwargs):
        self.values[key] = value
        if "ex" in kwargs:
            self.ttls[key] = kwargs["ex"]

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def zadd(self, key, mapping, nx=False, **kwargs):
        zset = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            if nx and member in zset:
                continue
            zset[member] = score

    def zrange(self, key, start, stop):
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        members = [member for member, _ in ordered]
        if stop < 0:
            stop = len(members) + stop
        return members[start : stop + 1]

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def delete(self, key):
        self.values.pop(key, None)


def _state(session_id: str, owner: str, updated_at: str, turn_count: int = 1) -> dict:
    return {
        "session_id": session_id,
        "owner_id": owner,
        "_version": 0,
        "turn_count": turn_count,
        "updated_at": updated_at,
        "messages": [{"role": "user", "content": f"问题 {session_id}"}],
        "summary": "",
        "risk_assessed": False,
    }


def test_save_state_indexes_session_for_listing() -> None:
    redis = _FakeRedis()
    adapter = RedisSessionAdapter(client=redis)
    adapter.ttl_seconds = 3600

    adapter.save_state(_state("mg_a1", "alice", "2026-11-20T10:00:00+00:00"))
    adapter.save_state(_state("mg_a2", "alice", "2026-11-20T11:00:00+00:00"))
    adapter.save_state(_state("mg_b1", "bob", "2026-11-20T10:30:00+00:00"))

    assert {item["session_id"] for item in adapter.list_states("alice")} == {"mg_a1", "mg_a2"}
    assert [item["session_id"] for item in adapter.list_states("bob")] == ["mg_b1"]
    assert adapter.list_states("carol") == []


def test_list_states_skips_expired_and_corrupt_entries() -> None:
    redis = _FakeRedis()
    adapter = RedisSessionAdapter(client=redis)

    adapter.save_state(_state("mg_live", "alice", "2026-11-20T10:00:00+00:00"))
    adapter.save_state(_state("mg_gone", "alice", "2026-11-20T10:00:00+00:00"))
    adapter.save_state(_state("mg_bad", "alice", "2026-11-20T10:00:00+00:00"))
    redis.delete(adapter._key("mg_gone"))  # Expired after being indexed.
    redis.values[adapter._key("mg_bad")] = "{not-json"  # Corrupt payload.
    redis.zadd(adapter._index_key("alice"), {"mg_phantom": 1})  # Never existed.

    assert [item["session_id"] for item in adapter.list_states("alice")] == ["mg_live"]


def test_list_states_skips_entries_of_other_owners() -> None:
    redis = _FakeRedis()
    adapter = RedisSessionAdapter(client=redis)

    adapter.save_state(_state("mg_b1", "bob", "2026-11-20T10:00:00+00:00"))
    redis.zadd(adapter._index_key("alice"), {"mg_b1": 1})  # Misplaced member.

    assert adapter.list_states("alice") == []
    assert [item["session_id"] for item in adapter.list_states("bob")] == ["mg_b1"]


def test_index_ttl_refreshes_on_save() -> None:
    redis = _FakeRedis()
    adapter = RedisSessionAdapter(client=redis)
    adapter.ttl_seconds = 3600
    state = _state("mg_a1", "alice", "2026-11-20T10:00:00+00:00")

    adapter.save_state(state)
    assert redis.ttls[adapter._index_key("alice")] == 3600

    adapter.ttl_seconds = 7200
    adapter.save_state({**state, "_version": 1})
    assert redis.ttls[adapter._index_key("alice")] == 7200


def test_fallback_write_indexes_without_pipeline() -> None:
    redis = _FakeRedis(with_pipeline=False)
    adapter = RedisSessionAdapter(client=redis)

    assert adapter.save_state(_state("mg_a1", "alice", "2026-11-20T10:00:00+00:00")) is True
    assert [item["session_id"] for item in adapter.list_states("alice")] == ["mg_a1"]


def test_store_list_sessions_uses_redis_index_newest_first() -> None:
    redis = _FakeRedis()
    adapter = RedisSessionAdapter(client=redis)
    store = SessionStore(adapter, mode="test")

    first = store.create(owner_id="alice")
    second = store.create(owner_id="alice")
    for created, updated_at in (
        (first, "2026-11-20T10:00:00+00:00"),
        (second, "2026-11-20T11:00:00+00:00"),
    ):
        state = {
            **created,
            "_version": 1,
            "turn_count": 1,
            "updated_at": updated_at,
            "messages": [{"role": "user", "content": "头痛怎么办"}],
        }
        assert adapter.save_state(state, expected_version=0) is True

    assert store.listing_complete is True
    entries = store.list_sessions("alice")
    assert [entry["session_id"] for entry in entries] == [
        second["session_id"],
        first["session_id"],
    ]
    assert entries[0]["title"] == "头痛怎么办"
    assert entries[0]["turn_count"] == 1
    assert store.list_sessions("bob") == []


def test_list_states_failure_surfaces_as_backend_error() -> None:
    class FailingRedis(_FakeRedis):
        def zrange(self, key, start, stop):
            raise ConnectionError("boom")

    store = SessionStore(RedisSessionAdapter(client=FailingRedis()), mode="test")

    with pytest.raises(SessionBackendError):
        store.list_sessions("alice")
