# @author: liuqinhe
"""阶段发射器：序号、终态、异常隔离。

最关键的一条是「回调坏掉不能连累编排」——阶段事件纯粹用于展示，
如果前端断连让 queue.put 抛异常，进而把整条 Agent 管线带崩，
那就是为了一个进度条搞坏了主流程。
"""

import pytest

from app.agents import stage


def _collector():
    got: list[dict] = []

    async def on_stage(evt: dict) -> None:
        got.append(evt)

    return got, on_stage


async def test_不传回调时整体是空操作():
    ran = False
    em = stage.StageEmitter()
    async with em.stage("intent_router", "正在识别"):
        ran = True
    assert ran is True


async def test_进入发running退出发终态():
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("intent_router", "正在识别问题类型") as st:
        st.done("已识别为接口报错")
    assert [(e["status"], e["label"]) for e in got] == [
        ("running", "正在识别问题类型"),
        ("success", "已识别为接口报错"),
    ]


async def test_同一key的两个事件共享序号():
    """前端据此原地更新那一行，而不是追加成两行。"""
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("intent_router", "识别"):
        pass
    assert {e["order"] for e in got} == {1}


async def test_不同key序号递增():
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("intent_router", "识别"):
        pass
    async with em.stage("api_diagnostic", "诊断"):
        pass
    assert [e["order"] for e in got] == [1, 1, 2, 2]


async def test_只有终态带耗时():
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("k", "跑"):
        pass
    assert "duration_ms" not in got[0]
    assert got[1]["duration_ms"] >= 0


async def test_未调用done时沿用进行中文案():
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("k", "正在处理"):
        pass
    assert got[1] == {"key": "k", "label": "正在处理", "status": "success",
                      "order": 1, "duration_ms": got[1]["duration_ms"]}


async def test_节点异常时发error并原样抛出():
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    with pytest.raises(ValueError, match="boom"):
        async with em.stage("k", "跑"):
            raise ValueError("boom")
    assert got[1]["status"] == "error"


async def test_error文案不带异常细节():
    """异常里可能有连接串、请求体，不能顺着 SSE 送到浏览器。"""
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    with pytest.raises(RuntimeError):
        async with em.stage("k", "跑"):
            raise RuntimeError("mysql://root:pwd@10.0.0.1 拒绝连接")
    assert "pwd" not in got[1]["label"]
    assert "10.0.0.1" not in got[1]["label"]


async def test_done可以把终态改成error():
    """单 Agent 异常被隔离、不抛出，节点得自己把终态标成失败。"""
    got, cb = _collector()
    em = stage.StageEmitter(on_stage=cb)
    async with em.stage("api_diagnostic", "诊断") as st:
        st.done("执行失败，已隔离", status="error")
    assert got[1]["status"] == "error"


async def test_回调抛异常不影响被包住的代码():
    async def broken(evt: dict) -> None:
        raise ConnectionResetError("客户端已断开")

    em = stage.StageEmitter(on_stage=broken)
    ran = False
    async with em.stage("k", "跑") as st:
        ran = True
        st.done("完成")
    assert ran is True


async def test_回调抛异常不影响节点返回值():
    async def broken(evt: dict) -> None:
        raise RuntimeError("queue full")

    em = stage.StageEmitter(on_stage=broken)

    async def node() -> str:
        async with em.stage("k", "跑"):
            return "结果"

    assert await node() == "结果"


async def test_意图文案覆盖全部路由意图():
    """漏一个意图会让前端显示成英文 key。"""
    from app.agents import intent_router

    assert set(stage.INTENT_LABELS) >= set(intent_router.INTENTS)
