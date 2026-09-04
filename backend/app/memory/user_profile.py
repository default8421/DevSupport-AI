# @author: liuqinhe
"""跨会话用户画像：按 tenant_id + user_id 累积结构化事实。

安全约束：
- 只存结构化派生值，不存自由文本，限制 prompt 注入面
- 入库前脱敏，防止用户贴过的密钥/PII 被长期固化后回显
- 仅作上下文，不得作为任何授权依据；工具鉴权一律走 ToolContext.tenant_id
- 所有读写都带 tenant_id 条件，防跨租户泄漏
"""

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import UserMemory

MAX_FACTS = 5       # 最多注入 5 条，避免长期记忆无声推高每轮 token
MAX_VALUE_LEN = 60

# 实体键 -> 记忆种类。request_id / http_status 刻意不入库：
# 一次性标识长期留存没有复用价值，且会让表无限膨胀。
_ENTITY_TO_KIND = {
    "endpoint": "endpoint",
    "error_code": "recurring_error",
    "app_id": "app_id",
}

_KIND_LABEL = {
    "endpoint": "常用",
    "app_id": "应用",
    "plan": "套餐",
}


def derive_facts(entities: dict, plan_name: str | None) -> list[tuple[str, str]]:
    """从实体与专家结果派生 (kind, value) 列表。"""
    facts: list[tuple[str, str]] = []
    for ent_key, kind in _ENTITY_TO_KIND.items():
        raw = (entities or {}).get(ent_key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        facts.append((kind, desensitize.desensitize_text(raw.strip())[:MAX_VALUE_LEN]))
    if plan_name and str(plan_name).strip():
        facts.append(("plan", str(plan_name).strip()[:MAX_VALUE_LEN]))
    return facts


async def remember(
    tenant_id: str, user_id: str, entities: dict, plan_name: str | None = None
) -> None:
    """把派生事实 upsert 进长期记忆，重复出现累加 hits。"""
    facts = derive_facts(entities, plan_name)
    if not facts or not tenant_id or not user_id:
        return
    async with AsyncSessionLocal() as s:
        for kind, value in facts:
            stmt = mysql_insert(UserMemory).values(
                tenant_id=tenant_id, user_id=user_id, kind=kind, value=value, hits=1
            )
            # 同一 (租户,用户,种类,值) 再次出现即累加，不新增行
            await s.execute(
                stmt.on_duplicate_key_update(hits=UserMemory.__table__.c.hits + 1)
            )
        await s.commit()


async def _fetch_facts(tenant_id: str, user_id: str) -> list[dict]:
    """取该用户 hits 最高的前 MAX_FACTS 条。查询必须带 tenant_id。"""
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(UserMemory.kind, UserMemory.value, UserMemory.hits)
                .where(UserMemory.tenant_id == tenant_id, UserMemory.user_id == user_id)
                .order_by(UserMemory.hits.desc(), UserMemory.updated_at.desc())
                .limit(MAX_FACTS)
            )
        ).all()
    return [{"kind": r.kind, "value": r.value, "hits": r.hits} for r in rows]


async def profile_line(tenant_id: str, user_id: str) -> str:
    """拼成一行紧凑的提示；无记录返回空串（不注入畸形空提示）。"""
    if not tenant_id or not user_id:
        return ""
    rows = await _fetch_facts(tenant_id, user_id)
    if not rows:
        return ""
    parts = []
    for r in rows[:MAX_FACTS]:
        if r["kind"] == "recurring_error":
            # hits>=2 才称"反复"，出现一次就说反复会误导诊断
            parts.append(
                f"近期反复遇到 {r['value']}（{r['hits']} 次）"
                if r["hits"] >= 2
                else f"曾遇到 {r['value']}"
            )
        else:
            parts.append(f"{_KIND_LABEL.get(r['kind'], r['kind'])} {r['value']}")
    return "该用户历史特征：" + "；".join(parts)
