# @author: liuqinhe
"""记忆的 MySQL 侧：会话历史回填与实体/意图落库。

session.py 保持纯 Redis（缓存），本模块承担持久化，两边职责不混。

存在的原因：Redis 历史有 6 小时 TTL，过期或进程冷启动后，MySQL 里明明有
完整对话，Agent 却会看到空历史，多轮指代（"刚才那个请求"）直接失效。
"""

from sqlalchemy import select, update

from app.db import AsyncSessionLocal
from app.memory import session
from app.models import Conversation, Message


async def recent_messages(conv_id: str, limit: int = 20) -> list[dict]:
    """取最近 limit 条消息，返回时间正序，用于回填 Redis。

    先按时间倒序取 N 条再反转，而不是正序取前 N 条——后者拿到的是最早的消息。
    created_at 为微秒精度（models.DateTime6），同一秒内多条也能稳定排序。
    """
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(Message.role, Message.content)
                .where(Message.conversation_id == conv_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        ).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


async def hydrated_history(conv_id: str) -> list[dict]:
    """分层读历史：Redis 命中直接用，落空则从 MySQL 回填并重建缓存。

    这层封装的意义在于把"缓存可能是空的"这件事收敛到一处，
    调用方不必每处都记得兜底。
    """
    history = await session.get_history(conv_id)
    if history:
        return history
    history = await recent_messages(conv_id, limit=session.HISTORY_MAX)
    if history:
        await session.replace_history(conv_id, history)
    return history


async def save_conversation_state(conv_id: str, *, entities: dict, intent: str | None) -> None:
    """把已收集实体与最近意图写回 conversation 表。

    这两列建表就有，但此前从未被写入。intent 为空说明本轮没识别出来，
    此时不写该列，避免把库里已有的意图擦成 null。
    """
    values: dict = {"collected_entities": entities or {}}
    if intent:
        values["latest_intent"] = intent
    async with AsyncSessionLocal() as s:
        await s.execute(update(Conversation).where(Conversation.id == conv_id).values(**values))
        await s.commit()
