# @author: liuqinhe
"""记忆回填：Redis 冷启动或 TTL 过期后，必须能从 MySQL 恢复历史。"""

import pytest

from app.memory import session, store


class FakeRedis:
    """够用的 Redis 替身：list + string + pipeline。供本文件与摘要测试复用。"""

    def __init__(self):
        self.data: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}

    async def lrange(self, key, start, end):
        items = self.data.get(key, [])
        return items if end == -1 else items[start : end + 1]

    async def rpush(self, key, val):
        self.data.setdefault(key, []).append(val)

    async def delete(self, key):
        self.data.pop(key, None)

    async def expire(self, key, ttl):
        return True

    async def ltrim(self, key, start, end):
        self.data[key] = self.data.get(key, [])[start:]

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, val, ex=None):
        self.kv[key] = val

    def pipeline(self):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def delete(self, key):
        self.ops.append(("delete", (key,)))
        return self

    def rpush(self, key, val):
        self.ops.append(("rpush", (key, val)))
        return self

    def expire(self, key, ttl):
        self.ops.append(("expire", (key, ttl)))
        return self

    async def execute(self):
        for op, args in self.ops:
            await getattr(self.r, op)(*args)
        self.ops.clear()


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(session, "get_redis", lambda: r)
    return r


async def test_replace_history重建为给定消息(fake_redis):
    await session.replace_history("c1", [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "第二句"},
    ])
    got = await session.get_history("c1")
    assert [m["content"] for m in got] == ["第一句", "第二句"]
    assert [m["role"] for m in got] == ["user", "assistant"]


async def test_replace_history先清空再写(fake_redis):
    await session.replace_history("c1", [{"role": "user", "content": "旧"}])
    await session.replace_history("c1", [{"role": "user", "content": "新"}])
    got = await session.get_history("c1")
    assert [m["content"] for m in got] == ["新"]


async def test_replace_history截断到窗口上限(fake_redis):
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    await session.replace_history("c1", msgs)
    got = await session.get_history("c1")
    assert len(got) == session.HISTORY_MAX
    assert got[-1]["content"] == "m29"  # 保留的是最近的，不是最早的


async def test_空列表不写入(fake_redis):
    await session.replace_history("c1", [])
    assert await session.get_history("c1") == []


class _Row:
    def __init__(self, role, content):
        self.role, self.content = role, content


def _fake_session(rows, captured):
    class _Result:
        def all(self):
            return rows

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            captured["stmt"] = str(stmt)
            return _Result()

        async def commit(self):
            captured["committed"] = True

    return lambda: _Sess()


async def test_recent_messages为时间正序(monkeypatch):
    """MySQL 侧按 created_at 倒序取最近 N 条，返回前必须反转成正序。"""
    captured = {}
    rows = [_Row("assistant", "第二句"), _Row("user", "第一句")]  # 倒序
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session(rows, captured))
    got = await store.recent_messages("c1", limit=20)
    assert [m["content"] for m in got] == ["第一句", "第二句"]
    assert "DESC" in captured["stmt"].upper()


async def test_recent_messages按会话过滤(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session([], captured))
    await store.recent_messages("c1")
    assert "conversation_id" in captured["stmt"]


async def test_缓存命中时不查库(fake_redis, monkeypatch):
    await session.replace_history("c1", [{"role": "user", "content": "缓存里的"}])

    def _boom():
        raise AssertionError("Redis 命中时不应查 MySQL")

    monkeypatch.setattr(store, "AsyncSessionLocal", _boom)
    got = await store.hydrated_history("c1")
    assert [m["content"] for m in got] == ["缓存里的"]


async def test_缓存落空时回填并重建缓存(fake_redis, monkeypatch):
    captured = {}
    rows = [_Row("assistant", "第二句"), _Row("user", "第一句")]
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session(rows, captured))

    got = await store.hydrated_history("c1")
    assert [m["content"] for m in got] == ["第一句", "第二句"]
    # 回填后 Redis 里必须有，下次才不用再查库
    assert [m["content"] for m in await session.get_history("c1")] == ["第一句", "第二句"]


async def test_库里也没有时返回空(fake_redis, monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session([], captured))
    assert await store.hydrated_history("c1") == []


async def test_save_conversation_state写实体与意图(monkeypatch):
    captured = {}
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session([], captured))
    await store.save_conversation_state("c1", entities={"request_id": "req_1"}, intent="api_error")
    assert captured["committed"] is True
    assert "UPDATE conversation" in captured["stmt"]


async def test_意图为空时不覆盖已有意图(monkeypatch):
    """intent 为 None 说明本轮没识别出来，不能把库里已有的意图擦成 null。"""
    captured = {}
    monkeypatch.setattr(store, "AsyncSessionLocal", _fake_session([], captured))
    await store.save_conversation_state("c1", entities={}, intent=None)
    assert "latest_intent" not in captured["stmt"]
