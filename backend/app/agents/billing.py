# @author: liuqinhe
"""Billing Agent：套餐/调用量/账单解释；高风险商业操作转人工。"""

import asyncio
import json
from dataclasses import dataclass, field

from app.agents import doc_rag, tool_loop
from app.agents.util import normalize_card, parse_json, render_card
from app.llm import client
from app.llm.router import model_for
from app.tools.registry import ToolContext

# 高风险商业意图关键词（不直接执行，转人工）
HIGH_RISK_KEYWORDS = [
    "退款", "退费", "改价", "调价", "重置",
    "降套餐", "降级", "降到", "升到", "改成", "套餐变更", "变更套餐",
    "改套餐", "换套餐", "退订", "解约", "取消套餐",
]

BILLING_TOOLS = ["query_plan", "query_usage", "query_bill"]

_BILLING_SYS = (
    "你是 API 平台账单助手的数据收集环节。根据用户问题调用工具取真实数据。\n"
    "- 涉及套餐配额/QPS 上限/单价 → query_plan\n"
    "- 涉及调用量 → query_usage（可传 month，如 2026-08）\n"
    "- 涉及账单金额/发票/费用构成 → query_bill\n"
    "账单为什么变化这类问题，需要同时取用量与账单做对比。\n"
    "数据够了就用一段话列出取到的关键数字，不要再调用工具，不要编造数字。"
)


@dataclass
class BillingResult:
    answer: str
    evidence: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    need_human: bool = False
    tokens: int = 0
    card: dict | None = None
    invocations: list[dict] = field(default_factory=list)
    degraded: bool = False


async def handle(query: str, entities: dict, ctx: ToolContext) -> BillingResult:
    """查套餐/用量/账单真实数据 + 计费文档，LLM 解释；高风险操作标记转人工。"""
    # 命中高风险商业关键词则只解释、不执行，最终标记 need_human
    high_risk = any(k in query for k in HIGH_RISK_KEYWORDS)

    month = entities.get("month")
    hint = f"\n【用户关注月份】{month}" if month else ""

    async def _data():
        return await tool_loop.run(
            system=_BILLING_SYS,
            query=f"{query}{hint}",
            tool_names=BILLING_TOOLS,
            ctx=ctx,
            model=model_for("billing_explain"),
        )

    async def _doc():
        return await doc_rag.answer(
            "套餐计费规则、账单费用构成与超额计费", tenant_id=ctx.tenant_id
        )

    loop_res, doc = await asyncio.gather(_data(), _doc())
    evidence = {
        "tool_findings": loop_res.content,
        "tool_calls": [{"name": i["name"], "ok": i["ok"]} for i in loop_res.invocations],
    }
    citations = doc.citations

    sys = (
        "你是 API 平台账单助手。基于【证据】(真实套餐/用量/账单数据)与【文档】解释账单/套餐问题。"
        "必须直接使用证据中的真实数字（套餐配额、各月调用量、基础/超额费用），不要说无法查看数据。"
        "账单上涨要对比月度用量并说明费用构成(基础费用 vs 超额费用)。"
        "退款、改价、套餐升降级等商业操作不能直接执行，需说明将转人工/商务。\n"
        "输出 JSON：{\"conclusion\":\"一句话结论\", \"evidence\":[\"引用到的真实数据点\"], \"steps\":[\"建议操作\"]}。只输出 JSON。"
    )
    risk_note = "\n注意：用户请求涉及高风险商业操作，结论中需明确告知不能直接执行、将转人工/商务跟进。" if high_risk else ""
    user = (
        f"【用户问题】{query}{risk_note}\n\n"
        f"【证据】{json.dumps(evidence, ensure_ascii=False)}\n\n"
        f"【文档】{doc.answer}"
    )
    gen = await client.chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model_for("billing_explain"),
        temperature=0.2,
    )
    card = normalize_card(parse_json(gen.content))
    if not card["conclusion"]:
        card["conclusion"] = gen.content.strip()[:200]
    return BillingResult(
        answer=render_card(card),
        evidence=evidence,
        citations=citations,
        # 熔断时数据不可信，与高风险操作一样转人工
        need_human=high_risk or loop_res.degraded,
        tokens=gen.total_tokens + doc.tokens + loop_res.tokens,
        card=card,
        invocations=loop_res.invocations,
        degraded=loop_res.degraded,
    )
