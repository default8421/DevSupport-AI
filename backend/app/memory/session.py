# @author: liuqinhe
"""会话记忆（Redis）：历史消息窗口 + 已收集实体。

实体记忆让多轮对话中无需重复追问（如已提供的 request_id 后续复用）。
"""

import json

from app.cache.redis_client import get_redis

HISTORY_MAX = 20        # 历史消息窗口
ENTITY_TTL = 60 * 60 * 6  # 实体记忆 6 小时
SUMMARY_KEEP = 10       # 压缩后保留的最近原文条数
SUMMARY_MAX_CHARS = 1000


def _hist_key(conv_id: str) -> str:
    return f"mem:hist:{conv_id}"


def _sum_key(conv_id: str) -> str:
    return f"mem:summary:{conv_id}"


def _ent_key(conv_id: str) -> str:
    return f"mem:ent:{conv_id}"


async def append_message(conv_id: str, role: str, content: str) -> None:
    r = get_redis()
    await r.rpush(_hist_key(conv_id), json.dumps({"role": role, "content": content}, ensure_ascii=False))
    await r.ltrim(_hist_key(conv_id), -HISTORY_MAX, -1)
    await r.expire(_hist_key(conv_id), ENTITY_TTL)


async def get_history(conv_id: str) -> list[dict]:
    r = get_redis()
    items = await r.lrange(_hist_key(conv_id), 0, -1)
    return [json.loads(x) for x in items]


async def replace_history(conv_id: str, messages: list[dict]) -> None:
    """用给定消息整体重建历史列表（MySQL 回填与摘要压缩后重置窗口用）。"""
    if not messages:
        return
    r = get_redis()
    key = _hist_key(conv_id)
    pipe = r.pipeline()
    pipe.delete(key)
    for m in messages[-HISTORY_MAX:]:
        pipe.rpush(key, json.dumps({"role": m["role"], "content": m["content"]}, ensure_ascii=False))
    pipe.expire(key, ENTITY_TTL)
    await pipe.execute()


async def get_summary(conv_id: str) -> str:
    """取超窗历史压缩出的滚动摘要；没有则空串。"""
    r = get_redis()
    raw = await r.get(_sum_key(conv_id))
    if not raw:
        return ""
    return raw if isinstance(raw, str) else raw.decode()


async def set_summary(conv_id: str, text: str) -> None:
    """写滚动摘要。空内容不写，避免把已有摘要擦掉。"""
    if not text or not text.strip():
        return
    r = get_redis()
    await r.set(_sum_key(conv_id), text.strip()[:SUMMARY_MAX_CHARS], ex=ENTITY_TTL)


async def get_entities(conv_id: str) -> dict:
    r = get_redis()
    raw = await r.get(_ent_key(conv_id))
    return json.loads(raw) if raw else {}


async def update_entities(conv_id: str, new_entities: dict) -> dict:
    """合并新抽取到的非空实体到记忆，返回合并后的实体。"""
    current = await get_entities(conv_id)
    for k, v in (new_entities or {}).items():
        if v not in (None, "", [], {}):
            current[k] = v
    r = get_redis()
    await r.set(_ent_key(conv_id), json.dumps(current, ensure_ascii=False), ex=ENTITY_TTL)
    return current
