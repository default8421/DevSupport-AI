# @author: liuqinhe
"""工具级阶段事件：调用前后各推一次，文案由后端给出。

节点级阶段已经告诉用户「正在分析接口报错」，但真正耗时的是里面的
工具调用。不把「正在查询调用日志」露出来，用户仍会在专家节点上干等。

三条不能破的约束：
1. on_stage 不传时 execute 行为与改造前完全一致；
2. 回调抛异常不能让工具执行失败——进度条搞坏主流程是本末倒置；
3. 校验拒绝 / 未知工具 / 高风险拦截不发阶段——那些是瞬时闸门，发了只会闪。
"""

import inspect

import pytest

from app.tools import registry
from app.tools.registry import ToolContext, ToolSpec, execute


@pytest.fixture(autouse=True)
def no_log(monkeypatch):
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(registry, "_log_call", _noop)


@pytest.fixture
def tool():
    """注册一个可替换行为的临时工具，测完立刻摘掉，避免污染全局 REGISTRY。"""
    state = {"fn": None}

    async def _func(args, ctx):
        if state["fn"]:
            return await state["fn"](args, ctx)
        return {"n": args.get("n", 1)}

    spec = ToolSpec(
        name="_stage_demo",
        description="测试用",
        parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
        func=_func,
        timeout=0.5,
        retries=0,
    )
    registry.REGISTRY[spec.name] = spec
    yield state
    registry.REGISTRY.pop(spec.name, None)


def _ctx(on_stage=None, **kw):
    return ToolContext(tenant_id="t1", trace_id="tr_1", on_stage=on_stage, **kw)


def _collector():
    got: list[dict] = []

    async def on_stage(evt: dict) -> None:
        got.append(evt)

    return got, on_stage


async def test_不传回调时execute照常返回():
    ctx = _ctx()
    # 构造函数签名里必须有这个可选字段，否则 supervisor 挂不上
    assert "on_stage" in inspect.signature(ToolContext).parameters
    r = await execute("query_ticket", {"ticket_id": "tk_x"}, ctx)
    # 没有库，query_ticket 会连不上；这里只断言「没因缺回调崩掉」
    assert "ok" in r


async def test_成功调用前后各发一次(tool):
    got, cb = _collector()
    r = await execute("_stage_demo", {"n": 2}, _ctx(cb))
    assert r["ok"] is True
    assert [e["status"] for e in got] == ["running", "success"]
    assert got[0]["key"] == "_stage_demo"
    assert "duration_ms" not in got[0]
    assert got[1]["duration_ms"] >= 0


async def test_失败时终态是error不是success(tool):
    async def boom(_args, _ctx):
        raise RuntimeError("mysql://root:pwd@10.0.0.1")

    tool["fn"] = boom
    got, cb = _collector()
    r = await execute("_stage_demo", {}, _ctx(cb))
    assert r["ok"] is False
    assert [e["status"] for e in got] == ["running", "error"]
    # 异常细节（含口令）不得进阶段文案
    assert "pwd" not in got[1]["label"]
    assert "10.0.0.1" not in got[1]["label"]


async def test_回调抛异常不影响工具返回(tool):
    async def broken(_evt: dict) -> None:
        raise ConnectionResetError("客户端已断开")

    r = await execute("_stage_demo", {"n": 1}, _ctx(broken))
    assert r == {"ok": True, "data": {"n": 1}}


async def test_校验拒绝不发阶段():
    got, cb = _collector()
    r = await execute("query_ticket", {}, _ctx(cb))  # 缺 ticket_id
    assert r["ok"] is False
    assert got == []


async def test_未知工具不发阶段():
    got, cb = _collector()
    r = await execute("drop_database", {}, _ctx(cb))
    assert r["ok"] is False
    assert got == []


async def test_高风险拦截不发阶段():
    got, cb = _collector()
    r = await execute("reset_api_key", {}, _ctx(cb, is_internal=False))
    assert r["ok"] is False
    assert got == []


async def test_已注册工具都有中文文案():
    """漏一个就会在前端显示英文函数名。"""
    from app.tools.registry import REGISTRY, TOOL_LABELS, load_tools

    load_tools()
    missing = [n for n in REGISTRY if n not in TOOL_LABELS]
    assert missing == []
    for label in TOOL_LABELS.values():
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label)


async def test_已知工具的进行中文案面向用户(tool):
    got, cb = _collector()
    await execute("query_call_log", {"request_id": "req_1"}, _ctx(cb))
    # query_call_log 会因没库失败，但 running 文案必须在调用前就发出
    assert got[0]["label"] == "正在查询调用日志"


async def test_supervisor把回调挂到ToolContext():
    """否则工具级阶段永远是空操作，节点级改完也看不见工具进度。"""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/agents/supervisor.py").read_text()
    tree = ast.parse(src)
    assigned = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ToolContext":
            keys = [k.arg for k in node.keywords]
            if "on_stage" in keys:
                assigned = True
    assert assigned, "supervisor.run 构造 ToolContext 时必须传入 on_stage"
