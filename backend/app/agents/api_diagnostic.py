# @author: liuqinhe
"""API Diagnostic Agent：基于调用日志/Key状态/限流统计 + 文档，辅助定位 API 报错。

输出结构：诊断结论 / 证据摘要 / 可能原因 / 建议操作 / 关联文档 / 是否需要工单。
"""

import asyncio
import json
from dataclasses import dataclass, field

from app.agents import doc_rag, tool_loop
from app.agents.util import normalize_card, parse_json, render_card
from app.db import AsyncSessionLocal
from app.llm import client
from app.llm.router import model_for
from app.models import ErrorCode
from app.tools.registry import ToolContext


async def _error_doc_fast(error_code: str):
    """已知错误码直接查 error_code 表，跳过完整 RAG（性能优化热路径）。"""
    async with AsyncSessionLocal() as s:
        row = await s.get(ErrorCode, error_code)
    if not row:
        return None
    answer = f"{row.code}（{row.name}）：{row.cause} 处理步骤：{row.fix_steps}"
    citations = [{"index": 1, "doc_title": "错误码手册", "section": row.code, "version": "v1", "score": 1.0}]
    return answer, citations


@dataclass
class DiagResult:
    answer: str
    evidence: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    error_code: str | None = None
    need_ticket: bool = False
    tokens: int = 0
    card: dict | None = None
    invocations: list[dict] = field(default_factory=list)  # 模型的工具决策过程，供 trace
    degraded: bool = False  # 工具熔断，证据不可信


DIAG_TOOLS = [
    "query_call_log",
    "query_apikey_status",
    "query_recent_call_stats",
    "check_signature_canonical",
]

_EVIDENCE_SYS = (
    "你是 API 平台诊断助手的证据收集环节。根据用户问题调用工具收集真实证据。\n"
    "工具选择原则：\n"
    "- 用户给了 request_id → 先 query_call_log，它会返回 error_code/endpoint/app_id 供后续使用\n"
    "- 怀疑 Key 过期或失效 → query_apikey_status\n"
    "- 涉及 429/限流/QPS 或想看错误率趋势 → query_recent_call_stats\n"
    "- 报 SIGN_INVALID 或 401 签名错误 → check_signature_canonical。"
    "注意它不校验 HMAC，secret_checked=false 时不得断言签名正确\n"
    "证据够了就用一段话总结收集到的关键事实，不要再调用工具。不要编造工具没返回的数据。"
)


def _call_log_missing(invocations: list[dict]) -> bool:
    """模型查过调用日志但没查到，或压根没查。两种情况都意味着证据不足。"""
    logs = [i for i in invocations if i["name"] == "query_call_log"]
    if not logs:
        return True
    return not any(i["ok"] and (i.get("data") or {}).get("found") for i in logs)


async def diagnose(query: str, entities: dict, ctx: ToolContext, *, is_rate_limit: bool = False) -> DiagResult:
    """模型自主收集证据（有界工具循环）+ 文档支撑，再组装结构化诊断卡片。"""
    error_code = entities.get("error_code")

    hint = f"\n【已识别实体】{json.dumps(entities, ensure_ascii=False)}" if entities else ""
    if is_rate_limit:
        hint += "\n【提示】本问题已判定为限流类，务必查看近期调用统计。"

    async def _evidence():
        return await tool_loop.run(
            system=_EVIDENCE_SYS,
            query=f"{query}{hint}",
            tool_names=DIAG_TOOLS,
            ctx=ctx,
            model=model_for("diagnose"),
        )

    async def _doc():
        # 热路径：已知错误码直查 error_code 表，跳过 RAG 的 embed/rerank/generate
        if error_code:
            fast = await _error_doc_fast(error_code)
            if fast:
                return fast[0], fast[1], 0
        d = await doc_rag.answer(
            f"{error_code} 含义、原因与处理步骤" if error_code else query,
            tenant_id=ctx.tenant_id,  # 只从服务端上下文取，不从实体或用户输入取
            error_code=error_code or None,
        )
        return d.answer, d.citations, d.tokens

    # 证据收集与文档检索仍然并行，保留原有的延迟特征
    loop_res, (doc_answer, citations, doc_tokens) = await asyncio.gather(_evidence(), _doc())

    evidence = {
        "tool_findings": loop_res.content,
        "tool_calls": [
            {"name": i["name"], "ok": i["ok"], "args": i["args"]} for i in loop_res.invocations
        ],
    }

    # 5. LLM 组装结构化诊断（JSON 卡片）
    sys = (
        "你是 API 平台诊断助手。基于【证据】(真实调用日志/Key状态/限流统计)和【文档】输出结构化诊断。"
        "只能依据证据与文档，不得编造；涉及密钥只展示脱敏值。\n"
        "输出 JSON：{\"conclusion\":\"一句话诊断结论\", \"evidence\":[\"关键证据(含状态码/错误码/时间/脱敏Key等)\"], "
        "\"steps\":[\"可执行修复步骤\"], \"need_ticket\":true/false}。need_ticket：证据不足以定位或需人工时为 true。只输出 JSON。"
    )
    user = (
        f"【用户问题】{query}\n\n"
        f"【证据】{json.dumps(evidence, ensure_ascii=False)}\n\n"
        f"【文档】{doc_answer}"
    )
    gen = await client.chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=model_for("diagnose"),
        temperature=0.2,
    )
    parsed = parse_json(gen.content)
    card = normalize_card(parsed)
    need_ticket = bool(parsed.get("need_ticket", False))
    # 工具连续失败（熔断）时证据不可信，直接转人工
    if loop_res.degraded:
        need_ticket = True
    # 确定性兜底：用户给了 request_id 却查不到日志，一律转人工，
    # 不依赖 LLM 自觉把 need_ticket 置真
    if entities.get("request_id") and _call_log_missing(loop_res.invocations):
        need_ticket = True
    if not card["conclusion"]:
        card["conclusion"] = gen.content.strip()[:200]

    return DiagResult(
        answer=render_card(card),
        evidence=evidence,
        citations=citations,
        error_code=error_code,
        need_ticket=need_ticket,
        tokens=gen.total_tokens + doc_tokens + loop_res.tokens,
        card=card,
        invocations=loop_res.invocations,
        degraded=loop_res.degraded,
    )
