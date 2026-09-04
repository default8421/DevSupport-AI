# @author: liuqinhe
"""对话 SSE 流：事件顺序、落库时机、异常传播、断连取消。

编排从「跑完再开流」改成「先开流、后台任务里跑」，多出三种坏法，
每种对应一组用例：
- 断连后编排任务成孤儿，继续跑完管线、继续烧 LLM 额度；
- 编排异常被后台任务吞掉，前端永远等不到 done；
- 助手消息的落库时机漂移，meta.message_id 对不上。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.api import chat as chat_api
from app.db import Base, get_db
from app.deps import CurrentUser, get_current_user
from app.main import app
from app.models import Conversation, Message
from app.schemas.chat import ChatRequest

ANSWER = "**结论**：时间戳超窗。\n\n1. 校准时钟\n2. 重新签名"

RESULT = {
    "answer": ANSWER,
    "intent": "api_error",
    "confidence": 0.93,
    "citations": [{"doc_title": "签名规范", "source": "builtin"}],
    "card": {"conclusion": "时间戳超窗", "evidence": [], "steps": []},
    "need_human": False,
    "ticket_id": None,
    "trace_id": "trace_x",
    "total_tokens": 100,
    "entities": {"request_id": "req_1"},
    "need_clarify": False,
    "from_cache": False,
}

STAGES = [
    {"key": "intent_router", "label": "正在识别问题类型", "status": "running", "order": 1},
    {"key": "intent_router", "label": "已识别为接口报错", "status": "success", "order": 1,
     "duration_ms": 12},
    {"key": "api_diagnostic", "label": "正在分析接口报错", "status": "running", "order": 2},
]


def _user(tenant="t1", uid="u1", role="customer_dev"):
    return CurrentUser(user_id=uid, username=uid, display_name=uid, role=role, tenant_id=tenant)


def parse_sse(raw: str) -> list[tuple[str, str]]:
    """把原始流按 SSE 规范拆成 (event, data)，data 的多行拼回换行。"""
    out = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
        if data:
            out.append((name, "\n".join(data)))
    return out


@pytest.fixture(autouse=True)
def _reset_sse_appstatus():
    """sse_starlette 把退出事件挂在模块级，会绑死在第一个事件循环上。

    TestClient 每个请求新建一个循环，不清掉就会在第二个用例开始报
    "bound to a different event loop"。属于库的全局状态，不是被测代码的问题。
    """
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


@pytest.fixture
def env(monkeypatch, tmp_path):
    """文件型 SQLite + 打桩 supervisor.run。

    用文件库而不是 :memory:：TestClient 在自己的事件循环里跑应用，
    与固件不共享连接，内存库建的表在应用那边看不见。
    """
    db_file = tmp_path / "chat.db"
    sync_eng = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_eng)

    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sess = async_sessionmaker(eng, expire_on_commit=False)

    async def _override_db():
        async with sess() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _user()

    class Env:
        client = TestClient(app)
        sessionmaker = sess

        @staticmethod
        def stub(stages=STAGES, result=None, raises=None, hang=False):
            """替掉编排：按需先推阶段事件，再返回结果 / 抛异常 / 挂住。"""
            state = {"cancelled": False, "kwargs": None, "started": asyncio.Event()}

            async def fake_run(**kwargs):
                state["kwargs"] = kwargs
                state["started"].set()
                on_stage = kwargs.get("on_stage")
                for evt in stages:
                    if on_stage:
                        await on_stage(evt)
                if hang:
                    try:
                        await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        state["cancelled"] = True
                        raise
                if raises:
                    raise raises
                return dict(result or RESULT)

            monkeypatch.setattr(chat_api.supervisor, "run", fake_run)
            return state

        @staticmethod
        def rows(model):
            with Session(sync_eng) as s:
                return list(s.execute(select(model)).scalars())

        @staticmethod
        def seed_conv(**kw):
            with Session(sync_eng) as s:
                s.add(Conversation(**kw))
                s.commit()

    yield Env()
    app.dependency_overrides.clear()


def _post(env, message="接口报签名错误", conv=None):
    return env.client.post("/api/chat", json={"message": message, "conversation_id": conv})


# ---------- 事件顺序与内容 ----------

def test_事件顺序为stage在前done在后(env):
    env.stub()
    events = parse_sse(_post(env).text)
    names = [e for e, _ in events]
    # 不写死 token 块数，那取决于分块大小，不是本用例要锁的东西
    assert names[:3] == ["stage"] * 3
    assert names[3] == "meta"
    assert set(names[4:-1]) == {"token"}
    assert names[-1] == "done"


def test_阶段事件原样透传(env):
    env.stub()
    import json

    stages = [json.loads(d) for e, d in parse_sse(_post(env).text) if e == "stage"]
    assert stages == STAGES


def test_token拼起来等于完整答案(env):
    env.stub()
    tokens = [d for e, d in parse_sse(_post(env).text) if e == "token"]
    assert "".join(tokens) == ANSWER


def test_done带齐结构化字段(env):
    import json

    env.stub()
    done = next(json.loads(d) for e, d in parse_sse(_post(env).text) if e == "done")
    assert done["answer"] == ANSWER
    assert done["card"]["conclusion"] == "时间戳超窗"
    assert done["citations"][0]["doc_title"] == "签名规范"
    assert done["trace_id"] == "trace_x"
    assert done["need_human"] is False


def test_没有阶段事件时流仍然完整(env):
    """旧编排（不回调 on_stage）也要能正常收尾。"""
    env.stub(stages=[])
    names = [e for e, _ in parse_sse(_post(env).text)]
    assert names[0] == "meta"
    assert names[-1] == "done"


# ---------- 落库时机 ----------

def test_助手消息在meta之前落库且id对得上(env):
    import json

    env.stub()
    meta = next(json.loads(d) for e, d in parse_sse(_post(env).text) if e == "meta")
    msgs = {m.id: m for m in env.rows(Message)}
    assert meta["message_id"] in msgs
    assert msgs[meta["message_id"]].role == "assistant"
    assert msgs[meta["message_id"]].content == ANSWER


def test_落库的meta带诊断元信息(env):
    env.stub()
    _post(env)
    msg = next(m for m in env.rows(Message) if m.role == "assistant")
    assert msg.meta["intent"] == "api_error"
    assert msg.meta["trace_id"] == "trace_x"
    assert msg.meta["card"]["conclusion"] == "时间戳超窗"


def test_用户消息也落库(env):
    env.stub()
    _post(env, message="我的接口 500 了")
    assert [m.content for m in env.rows(Message) if m.role == "user"] == ["我的接口 500 了"]


def test_会话意图被更新(env):
    env.stub()
    _post(env)
    assert env.rows(Conversation)[0].latest_intent == "api_error"


def test_需要转人工时会话被标记(env):
    env.stub(result={**RESULT, "need_human": True})
    _post(env)
    assert env.rows(Conversation)[0].transferred_to_human is True


# ---------- 异常传播 ----------

def test_编排异常发error而不是让前端干等(env):
    import json

    env.stub(raises=RuntimeError("milvus 挂了"))
    events = parse_sse(_post(env).text)
    names = [e for e, _ in events]
    assert "error" in names
    assert "done" not in names
    payload = json.loads(next(d for e, d in events if e == "error"))
    assert "重试" in payload["message"]


def test_error不泄漏异常细节(env):
    env.stub(raises=RuntimeError("mysql://root:pwd@10.0.0.1 连接失败"))
    raw = _post(env).text
    assert "pwd" not in raw
    assert "10.0.0.1" not in raw
    assert "RuntimeError" not in raw


def test_编排异常时不落助手消息(env):
    env.stub(raises=RuntimeError("boom"))
    _post(env)
    assert [m.role for m in env.rows(Message)] == ["user"]


def test_异常前的阶段事件仍然发出(env):
    """失败也要让用户看见走到哪一步了。"""
    env.stub(raises=RuntimeError("boom"))
    names = [e for e, _ in parse_sse(_post(env).text)]
    assert names == ["stage", "stage", "stage", "error"]


# ---------- 断连取消 ----------

async def test_断连后编排任务被取消(env):
    """否则孤儿任务会跑完整条管线、继续烧 LLM 额度。"""
    state = env.stub(hang=True)
    async with env.sessionmaker() as db:
        resp = await chat_api.chat(
            ChatRequest(message="hi", conversation_id=None), user=_user(), db=db
        )
        gen = resp.body_iterator
        assert (await gen.__anext__())["event"] == "stage"  # 编排已在跑
        await gen.aclose()  # 模拟客户端断开
        for _ in range(10):  # 让取消传播到后台任务
            await asyncio.sleep(0)
    assert state["cancelled"] is True


async def test_正常收尾后没有悬挂任务(env):
    env.stub()
    before = len(asyncio.all_tasks())
    async with env.sessionmaker() as db:
        resp = await chat_api.chat(
            ChatRequest(message="hi", conversation_id=None), user=_user(), db=db
        )
        async for _ in resp.body_iterator:
            pass
    await asyncio.sleep(0)
    assert len(asyncio.all_tasks()) <= before


# ---------- 身份与人工模式 ----------

def test_编排收到服务端注入的身份(env):
    state = env.stub()
    _post(env)
    kw = state["kwargs"]
    assert kw["tenant_id"] == "t1"
    assert kw["user_id"] == "u1"
    assert kw["is_internal"] is False
    assert callable(kw["on_stage"])


def test_人工模式不走编排也不发stage(env):
    env.seed_conv(id="conv_h", tenant_id="t1", user_id="u1", channel="web",
                  status="active", transferred_to_human=True)
    state = env.stub()
    events = parse_sse(_post(env, conv="conv_h").text)
    names = {e for e, _ in events}
    assert names == {"meta", "token", "done"}
    assert state["kwargs"] is None
