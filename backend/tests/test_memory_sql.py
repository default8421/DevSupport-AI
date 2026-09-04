# @author: liuqinhe
"""记忆层 SQL 跑在真实引擎（SQLite）上，而不是只对着假对象断言。

假对象返回预设行，"倒序取 N 条再反转"这类逻辑是否真的成立测不出来。
本地拉不到 MySQL 镜像时，SQLite 至少能验证排序、过滤与更新语义。
MySQL 方言特有的 upsert 不在此覆盖，见 test_user_profile.py。
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.memory import store
from app.models import Conversation, Message

# 与 Message.created_at 的列类型一致（项目用 naive DATETIME，见 models._now）
BASE_TIME = datetime(2026, 9, 4, 10, 0, 0)  # noqa: DTZ001


@pytest.fixture
async def db(monkeypatch):
    """内存库 + 两个会话的 25 条消息，故意乱序插入。"""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sess = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(store, "AsyncSessionLocal", sess)

    async with sess() as s:
        s.add(Conversation(id="c1", tenant_id="t1", user_id="u1"))
        # 乱序插入：若排序依赖插入顺序而非 created_at，这里就会暴露
        for i in [3, 0, 7, 1, 9, 2, 5, 4, 8, 6, *range(10, 25)]:
            s.add(
                Message(
                    id=f"m{i:03d}",
                    conversation_id="c1",
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"消息{i}",
                    created_at=BASE_TIME + timedelta(seconds=i),
                )
            )
        s.add(
            Message(
                id="other", conversation_id="c2", role="user",
                content="别的会话", created_at=BASE_TIME,
            )
        )
        await s.commit()
    yield sess
    await eng.dispose()


async def test_取的是最近N条而非最早N条(db):
    got = await store.recent_messages("c1", limit=20)
    assert [m["content"] for m in got] == [f"消息{i}" for i in range(5, 25)]


async def test_返回时间正序(db):
    got = await store.recent_messages("c1", limit=5)
    assert [m["content"] for m in got] == ["消息20", "消息21", "消息22", "消息23", "消息24"]


async def test_不串会话(db):
    got = await store.recent_messages("c1", limit=25)
    assert "别的会话" not in [m["content"] for m in got]


async def test_role随消息正确映射(db):
    got = await store.recent_messages("c1", limit=2)
    assert [m["role"] for m in got] == ["assistant", "user"]  # 消息23 奇数, 消息24 偶数


async def test_空会话返回空列表(db):
    assert await store.recent_messages("c_missing") == []


async def test_实体与意图真的落库(db):
    await store.save_conversation_state("c1", entities={"request_id": "req_9"}, intent="api_error")
    async with db() as s:
        conv = await s.get(Conversation, "c1")
        assert conv.collected_entities == {"request_id": "req_9"}
        assert conv.latest_intent == "api_error"


async def test_intent为空不擦掉已有意图(db):
    await store.save_conversation_state("c1", entities={}, intent="billing")
    await store.save_conversation_state("c1", entities={"month": "2026-08"}, intent=None)
    async with db() as s:
        conv = await s.get(Conversation, "c1")
        assert conv.latest_intent == "billing"
        assert conv.collected_entities == {"month": "2026-08"}


async def test_回填走真实库(db, monkeypatch):
    """hydrated_history 在缓存落空时确实从库里取到数据。"""
    from app.memory import session
    from tests.test_memory_store import FakeRedis

    redis = FakeRedis()  # 必须复用同一实例，否则读写落在不同的假 Redis 上
    monkeypatch.setattr(session, "get_redis", lambda: redis)
    got = await store.hydrated_history("c1")
    assert [m["content"] for m in got] == [f"消息{i}" for i in range(5, 25)]
    # 回填后缓存里也有了
    assert len(await session.get_history("c1")) == session.HISTORY_MAX
