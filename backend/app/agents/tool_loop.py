# @author: liuqinhe
"""有界工具循环：模型在受限工具子集内选工具、填参数，迭代次数有上限。

为什么不做全自主 ReAct：把全部工具暴露给模型会 token 膨胀且选错工具率高。
这里每个专家 Agent 只传自己那 3-5 个工具名，模型只负责"选哪个 + 填什么参"，
整体路径仍由 LangGraph 确定性编排。入参校验在 registry.execute 内部完成。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

from app.llm import client
from app.tools import registry
from app.tools.registry import ToolContext

HISTORY_TURNS = 4             # 带入循环的历史消息条数
TOOL_RESULT_MAX_CHARS = 4000  # 单条工具结果注回 prompt 的长度上限
FAIL_ROUNDS_TO_BREAK = 2      # 连续几轮全败即熔断


@dataclass
class ToolLoopResult:
    content: str = ""
    invocations: list[dict] = field(default_factory=list)  # 供 trace 展示模型的决策过程
    tokens: int = 0
    degraded: bool = False  # 熔断：调用方据此转人工


async def _invoke(tc: dict, ctx: ToolContext) -> tuple[dict, dict]:
    """执行单次工具调用，返回 (注回 prompt 的载荷, trace 用的调用记录)。"""
    name = tc.get("name", "")
    start = time.perf_counter()
    try:
        args = json.loads(tc.get("arguments") or "{}")
        if not isinstance(args, dict):
            raise TypeError("arguments 不是 JSON 对象")
    except Exception as e:  # noqa: BLE001  模型可能吐出非法 JSON，不能让它崩掉整轮
        err = f"参数不是合法 JSON: {e}"
        return (
            {"ok": False, "error": err},
            {"name": name, "args": {}, "ok": False, "duration_ms": 0, "error": err},
        )

    payload = await registry.execute(name, args, ctx)
    return (
        payload,
        {
            "name": name,
            "args": args,
            "ok": bool(payload.get("ok")),
            # 带上返回数据，调用方才能对模型的判断做确定性兜底（这份记录不落库）
            "data": payload.get("data"),
            "duration_ms": int((time.perf_counter() - start) * 1000),
            "error": payload.get("error"),
        },
    )


async def run(
    *,
    system: str,
    query: str,
    tool_names: list[str],
    ctx: ToolContext,
    model: str,
    history: list[dict] | None = None,
    max_iters: int = 3,
) -> ToolLoopResult:
    """让模型在 tool_names 范围内自主调用工具，最多 max_iters 轮。"""
    tools = registry.tools_for(tool_names)
    messages: list[dict] = [{"role": "system", "content": system}]
    for m in (history or [])[-HISTORY_TURNS:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": query})

    result = ToolLoopResult()
    fail_rounds = 0

    for _ in range(max_iters):
        r = await client.chat(messages, model=model, tools=tools)
        result.tokens += r.total_tokens

        if not r.tool_calls:
            result.content = (r.content or "").strip()
            return result

        messages.append(
            {
                "role": "assistant",
                "content": r.content or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in r.tool_calls
                ],
            }
        )

        outcomes = await asyncio.gather(*[_invoke(tc, ctx) for tc in r.tool_calls])
        any_ok = False
        for tc, (payload, inv) in zip(r.tool_calls, outcomes):
            result.invocations.append(inv)
            any_ok = any_ok or inv["ok"]
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(payload, ensure_ascii=False, default=str)[
                        :TOOL_RESULT_MAX_CHARS
                    ],
                }
            )

        fail_rounds = 0 if any_ok else fail_rounds + 1
        if fail_rounds >= FAIL_ROUNDS_TO_BREAK:
            result.degraded = True
            break

    # 迭代用尽或熔断：再要一次不带 tools 的收尾，保证用户拿到文本而不是空答复
    closing = await client.chat(
        messages + [{"role": "user", "content": "基于以上工具结果给出最终答复，不要再调用工具。"}],
        model=model,
    )
    result.tokens += closing.total_tokens
    result.content = (closing.content or "").strip()
    return result
