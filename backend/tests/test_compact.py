# @author: liuqinhe
"""滚动摘要：历史超窗时把最旧的压成摘要，保留最近若干条原文。

此前超窗直接 ltrim 丢弃，长对话必然失忆。
"""

import pytest

from app.llm.client import ChatResult
from app.memory import compact, session
from tests.test_memory_store import FakeRedis


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(session, "get_redis", lambda: r)
    return r


async def _fill(n: int) -> None:
    await session.replace_history("c1", [{"role": "user", "content": f"m{i}"} for i in range(n)])


async def test_未超窗不压缩(fake_redis, monkeypatch):
    await _fill(5)

    async def _boom(*a, **kw):
        raise AssertionError("未超窗不应调用 LLM")

    monkeypatch.setattr(compact.client, "chat", _boom)
    assert await compact.maybe_compact("c1") is False


async def test_超窗时压缩并保留最近N条(fake_redis, monkeypatch):
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        return ChatResult(content="用户先问了签名再问了限流", total_tokens=8)

    monkeypatch.setattr(compact.client, "chat", _chat)
    assert await compact.maybe_compact("c1") is True

    kept = await session.get_history("c1")
    assert len(kept) == session.SUMMARY_KEEP
    assert kept[-1]["content"] == f"m{session.HISTORY_MAX - 1}"  # 留的是最近的
    assert "签名" in await session.get_summary("c1")


async def test_摘要落库前脱敏(fake_redis, monkeypatch):
    """摘要由用户内容派生且会回灌 prompt，密钥不能被长期固化。"""
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        return ChatResult(
            content="用户的 key 是 ak_demo_abcdef1234567890abcdef12", total_tokens=5
        )

    monkeypatch.setattr(compact.client, "chat", _chat)
    await compact.maybe_compact("c1")
    assert "ak_demo_abcdef1234567890abcdef12" not in await session.get_summary("c1")


async def test_已有摘要时做增量合并(fake_redis, monkeypatch):
    await session.set_summary("c1", "此前：用户在排查 401")
    await _fill(session.HISTORY_MAX)
    seen = {}

    async def _chat(messages, **kw):
        seen["prompt"] = "\n".join(m["content"] for m in messages)
        return ChatResult(content="合并后的摘要", total_tokens=5)

    monkeypatch.setattr(compact.client, "chat", _chat)
    await compact.maybe_compact("c1")
    assert "此前：用户在排查 401" in seen["prompt"]  # 旧摘要必须参与合并
    assert await session.get_summary("c1") == "合并后的摘要"


async def test_模型给空摘要时不覆盖已有(fake_redis, monkeypatch):
    await session.set_summary("c1", "原摘要")
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        return ChatResult(content="   ", total_tokens=1)

    monkeypatch.setattr(compact.client, "chat", _chat)
    await compact.maybe_compact("c1")
    assert await session.get_summary("c1") == "原摘要"


async def test_模型给空摘要时仍然收窗(fake_redis, monkeypatch):
    """否则每轮都会重试压缩，白烧 token。"""
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        return ChatResult(content="", total_tokens=1)

    monkeypatch.setattr(compact.client, "chat", _chat)
    assert await compact.maybe_compact("c1") is True
    assert len(await session.get_history("c1")) == session.SUMMARY_KEEP


async def test_LLM异常不影响主流程(fake_redis, monkeypatch):
    """压缩是尽力而为的优化，失败了不能让整轮对话报错。"""
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        raise RuntimeError("模型服务不可用")

    monkeypatch.setattr(compact.client, "chat", _chat)
    assert await compact.maybe_compact("c1") is False
    # 压缩失败则历史保持原样，下轮再试
    assert len(await session.get_history("c1")) == session.HISTORY_MAX


async def test_摘要有长度上限(fake_redis, monkeypatch):
    await _fill(session.HISTORY_MAX)

    async def _chat(messages, **kw):
        return ChatResult(content="很长的摘要" * 1000, total_tokens=5)

    monkeypatch.setattr(compact.client, "chat", _chat)
    await compact.maybe_compact("c1")
    assert len(await session.get_summary("c1")) <= session.SUMMARY_MAX_CHARS


async def test_摘要读写(fake_redis):
    assert await session.get_summary("c_none") == ""
    await session.set_summary("c1", "一段摘要")
    assert await session.get_summary("c1") == "一段摘要"
    # 空摘要不写入，避免把已有的擦掉
    await session.set_summary("c1", "  ")
    assert await session.get_summary("c1") == "一段摘要"
