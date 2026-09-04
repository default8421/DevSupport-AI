# @author: liuqinhe
"""智能对话接口（SSE 流式）。

流程：落库会话/用户消息 → 开流 → 编排在后台任务里跑、阶段事件实时推出 →
编排结束后落库助手消息 → 发 meta → 逐块吐答案 → done。

事件顺序：`stage*` → `meta` → `token*` → `done`（异常时以 `error` 收尾）。
`stage` 与 `error` 是新增的，`meta`/`token`/`done` 的字段一个都没改，
旧前端对未知事件是静默忽略，因此前后端可以分开上线。

为什么编排要挪进生成器：改造前是「先 await 跑完编排、再开 SSE」，
用户按下发送后要盯着空白等数秒。现在先把响应开出去，编排在后台任务里跑，
用 asyncio.Queue 把阶段事件喂给生成器，等待期间屏幕上是有东西的。
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agents import supervisor
from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.models import Conversation, Message
from app.schemas.chat import ChatRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# 打字机节奏：每块字符数与块间隔
CHUNK_SIZE = 18
CHUNK_DELAY = 0.02


def _gen(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def _get_or_create_conversation(
    db: AsyncSession, conv_id: str | None, user: CurrentUser
) -> Conversation:
    if conv_id:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one_or_none()
        # 复用已有会话前校验租户归属，防止越权访问他人会话
        if conv and (user.is_internal or conv.tenant_id == user.tenant_id):
            return conv
    conv = Conversation(id=_gen("conv"), tenant_id=user.tenant_id, user_id=user.user_id,
                        channel="web", status="active")
    db.add(conv)
    await db.commit()
    return conv


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _get_or_create_conversation(db, body.conversation_id, user)

    # 人工模式：会话已转人工 → 客户消息不再走 AI，仅追加并提示等待人工
    if conv.transferred_to_human and not user.is_internal:
        cust_msg = Message(id=_gen("msg"), conversation_id=conv.id, role="user", content=body.message)
        db.add(cust_msg)
        await db.commit()
        ack = "您的消息已转达人工技术支持，我们会尽快回复，可在「我的会话」查看进展。"

        async def human_stream():
            yield {"event": "meta", "data": json.dumps(
                {"conversation_id": conv.id, "message_id": cust_msg.id, "intent": "human", "trace_id": None},
                ensure_ascii=False)}
            for i in range(0, len(ack), 12):
                yield {"event": "token", "data": ack[i:i + 12]}
                await asyncio.sleep(0.02)
            yield {"event": "done", "data": json.dumps({"human_mode": True}, ensure_ascii=False)}

        return EventSourceResponse(human_stream())

    async def _persist_assistant(result: dict) -> Message:
        """落库助手消息并更新会话状态。

        时机不能动：必须在编排结束之后、meta 之前——meta.message_id 依赖它。
        """
        msg = Message(
            id=_gen("msg"), conversation_id=conv.id, role="assistant",
            content=result["answer"],
            meta={
                "intent": result.get("intent"),
                "citations": result.get("citations", []),
                "card": result.get("card"),
                "trace_id": result.get("trace_id"),
                "ticket_id": result.get("ticket_id"),
                "need_human": result.get("need_human"),
                "from_cache": result.get("from_cache", False),
            },
        )
        db.add(msg)
        conv.latest_intent = result.get("intent")
        if result.get("need_human"):
            conv.transferred_to_human = True
        await db.commit()
        return msg

    # 落库用户消息
    user_msg = Message(id=_gen("msg"), conversation_id=conv.id, role="user", content=body.message)
    db.add(user_msg)
    await db.commit()

    async def event_stream():
        # 队列里三种消息：("stage", 事件) 编排中途；("result", 结果) 正常收尾；("failed", 异常)
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def on_stage(evt: dict) -> None:
            await q.put(("stage", evt))

        async def orchestrate() -> None:
            try:
                result = await supervisor.run(
                    query=body.message, tenant_id=conv.tenant_id, user_id=user.user_id,
                    conversation_id=conv.id, is_internal=user.is_internal, on_stage=on_stage,
                )
            except asyncio.CancelledError:
                raise  # 断连取消，不当作业务失败
            except Exception as e:  # noqa: BLE001  异常经队列回传，否则前端永远等不到 done
                await q.put(("failed", e))
            else:
                await q.put(("result", result))

        task = asyncio.create_task(orchestrate())
        try:
            # 阶段事件边跑边发。队列是 FIFO，result 一定排在所有 stage 之后
            while True:
                kind, payload = await q.get()
                if kind == "stage":
                    yield {"event": "stage", "data": json.dumps(payload, ensure_ascii=False)}
                    continue
                break

            if kind == "failed":
                # 流已经开出去了，改不成 500；给前端一个可收尾的 error，异常照常进日志
                log.exception("编排失败 conversation=%s", conv.id, exc_info=payload)
                yield {"event": "error", "data": json.dumps(
                    {"message": "处理你的问题时出错了，请重试或转人工。", "conversation_id": conv.id},
                    ensure_ascii=False)}
                return

            result = payload
            assistant_msg = await _persist_assistant(result)

            yield {"event": "meta", "data": json.dumps(
                {"conversation_id": conv.id, "message_id": assistant_msg.id,
                 "intent": result.get("intent"), "trace_id": result.get("trace_id")},
                ensure_ascii=False)}
            # 流式发送答案（按字符块模拟打字机）
            answer = result["answer"]
            for i in range(0, len(answer), CHUNK_SIZE):
                yield {"event": "token", "data": answer[i:i + CHUNK_SIZE]}
                await asyncio.sleep(CHUNK_DELAY)
            # 末尾发完整结构化信息
            yield {"event": "done", "data": json.dumps(
                {
                    "answer": answer,
                    "card": result.get("card"),
                    "citations": result.get("citations", []),
                    "ticket_id": result.get("ticket_id"),
                    "need_human": result.get("need_human", False),
                    "need_clarify": result.get("need_clarify", False),
                    "from_cache": result.get("from_cache", False),
                    "trace_id": result.get("trace_id"),
                }, ensure_ascii=False)}
        finally:
            # 客户端断连时生成器被关闭，编排任务必须一起取消，
            # 否则它会成为孤儿继续跑完整条管线、继续烧 LLM 额度
            task.cancel()

    return EventSourceResponse(event_stream())
