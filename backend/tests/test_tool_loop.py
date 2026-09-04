# @author: liuqinhe
"""有界工具循环：模型选工具+填参数，最多 N 轮，连续全败熔断。"""

import pytest

from app.agents import tool_loop
from app.llm.client import ChatResult
from app.tools.registry import ToolContext

CTX = ToolContext(tenant_id="t_test", trace_id="tr_test")


def _chat_returning(*results):
    """按顺序返回预置 ChatResult，并记录每次收到的 messages 与 tools。"""
    seen = []

    async def _fake(messages, *, model=None, tools=None, temperature=0.2):
        seen.append({"messages": list(messages), "tools": tools})
        return results[min(len(seen) - 1, len(results) - 1)]

    _fake.seen = seen
    return _fake


async def _noop_log(*a, **kw):
    """工具调用日志要写 MySQL，单测里不连库。"""


def _tool_message(messages: list[dict]) -> dict:
    """取回灌给模型的第一条工具结果消息。"""
    return next(m for m in messages if m["role"] == "tool")


@pytest.fixture(autouse=True)
def _no_real_tools(monkeypatch):
    """不依赖真实注册表：任何工具名都给一份空 schema。"""
    monkeypatch.setattr(
        tool_loop.registry,
        "tools_for",
        lambda names: [
            {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
            for n in names
        ],
    )


async def test_模型不调工具时直接返回文本(monkeypatch):
    monkeypatch.setattr(
        tool_loop.client, "chat", _chat_returning(ChatResult(content="直接答复", total_tokens=10))
    )
    r = await tool_loop.run(system="s", query="q", tool_names=["demo"], ctx=CTX, model="m")
    assert r.content == "直接答复"
    assert r.invocations == [] and r.tokens == 10 and r.degraded is False


async def test_执行工具后带结果二次询问(monkeypatch):
    first = ChatResult(
        content="",
        tool_calls=[{"id": "c1", "name": "demo", "arguments": '{"request_id": "req_1"}'}],
        total_tokens=5,
    )
    second = ChatResult(content="根据日志得出结论", total_tokens=7)
    fake = _chat_returning(first, second)
    monkeypatch.setattr(tool_loop.client, "chat", fake)

    async def _exec(name, args, ctx):
        assert name == "demo" and args == {"request_id": "req_1"}
        return {"ok": True, "data": {"found": True}}

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)

    r = await tool_loop.run(system="s", query="q", tool_names=["demo"], ctx=CTX, model="m")
    assert r.content == "根据日志得出结论"
    assert r.tokens == 12
    assert len(r.invocations) == 1 and r.invocations[0]["ok"] is True
    # 第二次请求必须带上 assistant(tool_calls) 与 role=tool 两条消息
    roles = [m["role"] for m in fake.seen[1]["messages"]]
    assert "tool" in roles and roles.count("assistant") == 1


async def test_并行执行同一轮的多个工具调用(monkeypatch):
    first = ChatResult(
        content="",
        tool_calls=[
            {"id": "c1", "name": "a", "arguments": "{}"},
            {"id": "c2", "name": "b", "arguments": "{}"},
        ],
        total_tokens=5,
    )
    monkeypatch.setattr(
        tool_loop.client, "chat", _chat_returning(first, ChatResult(content="done", total_tokens=1))
    )
    called = []

    async def _exec(name, args, ctx):
        called.append(name)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)
    r = await tool_loop.run(system="s", query="q", tool_names=["a", "b"], ctx=CTX, model="m")
    assert sorted(called) == ["a", "b"] and len(r.invocations) == 2


async def test_非法json参数不崩且记为失败(monkeypatch):
    first = ChatResult(
        content="", tool_calls=[{"id": "c1", "name": "demo", "arguments": "{坏的"}], total_tokens=3
    )
    monkeypatch.setattr(
        tool_loop.client, "chat", _chat_returning(first, ChatResult(content="兜底", total_tokens=1))
    )

    async def _exec(name, args, ctx):
        raise AssertionError("非法 JSON 不应进入 execute")

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)
    r = await tool_loop.run(system="s", query="q", tool_names=["demo"], ctx=CTX, model="m")
    assert r.invocations[0]["ok"] is False


async def test_连续两轮全败触发熔断(monkeypatch):
    tc = ChatResult(
        content="", tool_calls=[{"id": "c", "name": "demo", "arguments": "{}"}], total_tokens=1
    )
    monkeypatch.setattr(tool_loop.client, "chat", _chat_returning(tc, tc, tc, tc))

    async def _exec(name, args, ctx):
        return {"ok": False, "error": "工具调用超时(>3s)"}

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)
    r = await tool_loop.run(
        system="s", query="q", tool_names=["demo"], ctx=CTX, model="m", max_iters=5
    )
    assert r.degraded is True
    assert len(r.invocations) == 2  # 第 3 轮不再发起


async def test_迭代用尽后强制不带工具收尾(monkeypatch):
    tc = ChatResult(
        content="", tool_calls=[{"id": "c", "name": "demo", "arguments": "{}"}], total_tokens=1
    )
    fake = _chat_returning(tc, tc, tc, ChatResult(content="最终答复", total_tokens=4))
    monkeypatch.setattr(tool_loop.client, "chat", fake)

    async def _exec(name, args, ctx):
        return {"ok": True, "data": {}}

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)
    r = await tool_loop.run(
        system="s", query="q", tool_names=["demo"], ctx=CTX, model="m", max_iters=3
    )
    assert r.content == "最终答复"
    assert fake.seen[-1]["tools"] is None  # 收尾那次不能再带 tools


async def test_历史只带最近若干条(monkeypatch):
    fake = _chat_returning(ChatResult(content="ok", total_tokens=1))
    monkeypatch.setattr(tool_loop.client, "chat", fake)
    history = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    await tool_loop.run(
        system="s", query="q", tool_names=["demo"], ctx=CTX, model="m", history=history
    )
    contents = [m["content"] for m in fake.seen[0]["messages"]]
    assert "m5" not in contents and "m6" in contents


async def test_真实注册表下模型填错参数被闸门拒绝并回灌(monkeypatch):
    """不打桩注册表：模型漏填 required，闸门必须拦下并把原因喂回模型。"""
    from app.tools import registry as real_registry

    real_registry.load_tools()
    monkeypatch.undo()  # 撤掉 _no_real_tools 的 tools_for 打桩
    monkeypatch.setattr(real_registry, "_log_call", _noop_log)

    bad = ChatResult(
        content="",
        # 漏了 required 的 timestamp
        tool_calls=[
            {
                "id": "c1",
                "name": "check_signature_canonical",
                "arguments": '{"params": {"a": "1"}}',
            }
        ],
        total_tokens=1,
    )
    fake = _chat_returning(bad, ChatResult(content="我需要你的时间戳", total_tokens=1))
    monkeypatch.setattr(tool_loop.client, "chat", fake)

    r = await tool_loop.run(
        system="s",
        query="签名报错",
        tool_names=["check_signature_canonical"],
        ctx=CTX,
        model="m",
    )
    assert r.invocations[0]["ok"] is False
    assert "timestamp" in r.invocations[0]["error"]
    # 拒绝原因必须回灌给模型，否则它无从修正
    tool_msg = _tool_message(fake.seen[1]["messages"])
    assert "参数校验失败" in tool_msg["content"]


async def test_真实注册表下正确调用签名工具(monkeypatch):
    from app.tools import registry as real_registry

    real_registry.load_tools()
    monkeypatch.undo()
    monkeypatch.setattr(real_registry, "_log_call", _noop_log)

    good = ChatResult(
        content="",
        tool_calls=[
            {
                "id": "c1",
                "name": "check_signature_canonical",
                "arguments": '{"params": {"name": "x"}, "timestamp": "1718000000123"}',
            }
        ],
        total_tokens=1,
    )
    fake = _chat_returning(good, ChatResult(content="你的时间戳是毫秒级", total_tokens=1))
    monkeypatch.setattr(tool_loop.client, "chat", fake)

    r = await tool_loop.run(
        system="s",
        query="签名报错",
        tool_names=["check_signature_canonical"],
        ctx=CTX,
        model="m",
    )
    assert r.invocations[0]["ok"] is True
    tool_msg = _tool_message(fake.seen[1]["messages"])
    assert "TIMESTAMP_UNIT" in tool_msg["content"]


async def test_工具结果过长会被截断(monkeypatch):
    first = ChatResult(
        content="", tool_calls=[{"id": "c1", "name": "demo", "arguments": "{}"}], total_tokens=1
    )
    fake = _chat_returning(first, ChatResult(content="done", total_tokens=1))
    monkeypatch.setattr(tool_loop.client, "chat", fake)

    async def _exec(name, args, ctx):
        return {"ok": True, "data": {"blob": "x" * 20000}}

    monkeypatch.setattr(tool_loop.registry, "execute", _exec)
    await tool_loop.run(system="s", query="q", tool_names=["demo"], ctx=CTX, model="m")
    tool_msg = _tool_message(fake.seen[1]["messages"])
    assert len(tool_msg["content"]) <= tool_loop.TOOL_RESULT_MAX_CHARS
