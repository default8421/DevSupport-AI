# @author: liuqinhe
"""Supervisor：用 LangGraph 编排多 Agent，串行保证安全审查在最终回复前。

管线：load_context → intent → [clarify?] → specialists(并行) → ticket → summarize → security
每个节点记录 AgentTrace；任一专业 Agent 异常被隔离降级，不影响整体。
"""

import asyncio

from langgraph.graph import END, START, StateGraph

from app.agents import api_diagnostic, billing, doc_rag, intent_router, security, stage, ticket
from app.agents.state import AgentState
from app.agents.util import render_card
from app.cache import route_cache, semantic_cache
from app.config import settings
from app.guardrail import fallback
from app.llm import client
from app.llm.router import model_for
from app.memory import compact, session, user_profile
from app.memory import store as mem_store
from app.observability import cost
from app.observability.trace import TraceCollector, timer
from app.tools.registry import ToolContext, load_tools

load_tools()


CHITCHAT_PROMPT = (
    "你是 DevSupport AI 助手。你可以："
    "1. 根据本租户上传到知识库的文档回答项目与业务问题；"
    "2. 解答本平台 API 接入、报错、账单等技术支持问题。"
    "用户说想问某项目、或刚上传了文档时，请邀请他们提出具体问题，不要说「联系业务负责人」或「我只能回答 API」。"
    "闲聊时简短礼貌，并提示可以直接问知识库或 API 问题。"
)


async def _general_reply(query: str) -> tuple[str, int]:
    r = await client.chat(
        [
            {"role": "system", "content": CHITCHAT_PROMPT},
            {"role": "user", "content": query},
        ],
        model=model_for("chitchat"),
        temperature=0.5,
    )
    return r.content.strip(), r.total_tokens


# 专家 Agent 的阶段文案：进行中 / 正常结束
_AGENT_STAGES = {
    "api_diagnostic": ("正在分析接口报错", "已完成接口诊断"),
    "billing": ("正在核对账单与用量", "已完成账单核对"),
    "doc_rag": ("正在检索知识库", "已找到相关文档"),
}


def build_graph(ctx: ToolContext, trace: TraceCollector, stages: stage.StageEmitter):
    """按请求构建图（闭包捕获 ctx、trace 与阶段发射器）。

    只有真正耗时的节点才发阶段事件：load_context 是两次缓存读取、单位是毫秒，
    发出去只会让前端闪一下，反而是噪声。
    """

    async def load_context(state: AgentState) -> dict:
        conv = state["conversation_id"]
        # Redis 冷启动或 TTL 过期时会从 MySQL 回填，否则库里明明有完整对话却失忆
        history, entities, profile, summary = await asyncio.gather(
            mem_store.hydrated_history(conv),
            session.get_entities(conv),
            user_profile.profile_line(state["tenant_id"], state.get("user_id", "")),
            session.get_summary(conv),
        )
        return {
            "history": history,
            "collected_entities": entities,
            "user_profile": profile,
            "history_summary": summary,
        }

    async def intent_node(state: AgentState) -> dict:
        history = state.get("history")
        summary = state.get("history_summary")
        if summary and history:
            # 摘要作为最早一条上下文参与指代消解（"刚才那个请求"可能在超窗部分）
            history = [{"role": "system", "content": f"早前对话摘要：{summary}"}, *history]
        async with stages.stage("intent_router", "正在识别问题类型") as st:
            with timer() as t:
                cached_route = None if history else await route_cache.get(state["query"])
                if cached_route:
                    res = {**cached_route, "tokens": 0}
                else:
                    res = await intent_router.classify(state["query"], history)
                    if not history:
                        await route_cache.put(state["query"], res)
            # 合并记忆中的实体（新抽取覆盖/补充）
            merged = await session.update_entities(state["conversation_id"], res["entities"])
            trace.step(
                "intent_router",
                input_summary=state["query"],
                output_summary=f"intent={res['intent']} conf={res['confidence']} entities={merged}",
                duration_ms=t.ms,
                token_usage=res["tokens"],
            )
            st.done(f"已识别为{stage.INTENT_LABELS.get(res['intent'], res['intent'])}")
            # 澄清判定：API 报错/数据质量类缺关键定位信息时追问
            need_clarify = False
            clarify_q = ""
            if res["intent"] == "api_error" and not merged.get("request_id") and not merged.get("error_code"):
                need_clarify = True
                clarify_q = "为帮你准确定位，请提供以下任一信息：request_id、出错的接口名，或返回的错误码/HTTP状态码（最多 3 项即可）。"
            elif res["confidence"] < settings.intent_confidence_threshold and res["intent"] != "chitchat":
                need_clarify = True
                clarify_q = "我不太确定你的问题类型，能补充一下具体场景吗？比如是接口报错、账单费用，还是知识库/上传文档里的用法？"
            return {
                "intent": res["intent"],
                "confidence": res["confidence"],
                "entities": merged,
                "route": res["route"],
                "need_clarify": need_clarify,
                "clarify_question": clarify_q,
            }

    def after_intent(state: AgentState) -> str:
        if state.get("need_clarify"):
            return "clarify"
        if state.get("intent") == "doc_qa" and not state.get("history"):
            return "semantic_cache"
        return "specialists"

    async def clarify_node(state: AgentState) -> dict:
        trace.step("clarify", input_summary=state["query"], output_summary=state["clarify_question"])
        return {"final_answer": state["clarify_question"], "need_human": False}

    async def semantic_cache_node(state: AgentState) -> dict:
        async with stages.stage("semantic_cache", "正在检索相似问题") as st:
            with timer() as ct:
                cached, query_emb = await semantic_cache.get(state["tenant_id"], state["query"])
            if cached:
                trace.step("semantic_cache", input_summary=state["query"],
                           output_summary=f"命中 similarity={cached['similarity']}", duration_ms=ct.ms)
                st.done("已命中历史相似问题")
                return {
                    "final_answer": cached["answer"],
                    "rag_citations": cached["citations"],
                    "card": cached.get("card"),
                    "need_human": False,
                    "from_cache": True,
                    "query_embedding": query_emb,
                }
            trace.step("semantic_cache", input_summary=state["query"],
                       output_summary="未命中，继续执行 doc_rag", duration_ms=ct.ms)
            st.done("未命中，转为检索文档")
            return {"from_cache": False, "query_embedding": query_emb}

    def after_semantic_cache(state: AgentState) -> str:
        return "cached" if state.get("from_cache") else "miss"

    async def _run_agent(name: str, coro):
        running, ok = _AGENT_STAGES[name]
        # 阶段包在异常隔离外面：单 Agent 失败不抛出，得手工把终态改成 error
        async with stages.stage(name, running) as st:
            with timer() as t:
                try:
                    result = await coro
                except Exception as e:  # noqa: BLE001  单 Agent 异常隔离
                    st.done("执行失败，已隔离", status="error")
                    return name, None, t, f"{type(e).__name__}: {e}"
            st.done("已降级返回（工具连续失败）" if getattr(result, "degraded", False) else ok)
            return name, result, t, None

    async def specialists_node(state: AgentState) -> dict:
        route = state.get("route", [])
        query, entities = state["query"], state.get("entities", {})
        outputs: dict = {}
        citations: list = []
        need_human = False
        need_ticket = False

        if not route:  # chitchat
            async with stages.stage("general", "正在生成回复") as st:
                with timer() as t:
                    reply, tok = await _general_reply(query)
                trace.step("general", input_summary=query, output_summary=reply[:120], duration_ms=t.ms, token_usage=tok)
                st.done("已生成回复")
                return {"agent_outputs": {"general": reply}, "rag_citations": []}

        # 跨会话画像只喂给证据收集环节，帮模型选对工具；不参与任何鉴权判断
        profile = state.get("user_profile") or ""
        q = f"{query}\n【{profile}】" if profile else query

        # 并行运行选中的专业 Agent
        coros = []
        if "api_diagnostic" in route:
            coros.append(_run_agent("api_diagnostic", api_diagnostic.diagnose(
                q, entities, ctx, is_rate_limit=state["intent"] == "rate_limit")))
        if "billing" in route:
            coros.append(_run_agent("billing", billing.handle(q, entities, ctx)))
        # doc_rag 仅在没有诊断/账单作为主答时单独使用，避免重复（诊断/账单内部已含文档）
        if "doc_rag" in route and not ("api_diagnostic" in route or "billing" in route):
            coros.append(_run_agent("doc_rag", doc_rag.answer(
                query, tenant_id=ctx.tenant_id, history=state.get("history"))))

        results = await asyncio.gather(*coros)
        for name, result, t, err in results:
            if err:
                trace.step(name, input_summary=query, status="error", duration_ms=t.ms, error=err)
                continue
            tok = getattr(result, "tokens", 0)
            hit = [c.get("doc_title") for c in getattr(result, "citations", [])]
            # 工具熔断要在链路上看得见，否则降级率无从统计
            prefix = "[熔断降级] " if getattr(result, "degraded", False) else ""
            trace.step(name, input_summary=query,
                       output_summary=prefix + getattr(result, "answer", "")[:160],
                       duration_ms=t.ms, token_usage=tok, hit_docs=hit)
            outputs[name] = result
            citations.extend(getattr(result, "citations", []))
            if getattr(result, "need_human", False):
                need_human = True
            if getattr(result, "need_ticket", False):
                need_ticket = True

        return {
            "agent_outputs": outputs,
            "rag_citations": citations,
            "need_human": need_human or state["intent"] == "ticket",
            "pending_ticket": need_ticket,
        }

    async def ticket_node(state: AgentState) -> dict:
        outputs = state.get("agent_outputs", {})
        need_ticket = state.get("pending_ticket") or state.get("need_human")
        if not need_ticket:
            return {}
        # 汇总现有诊断作为工单上下文
        ai_diag = ""
        evidence = {}
        if "api_diagnostic" in outputs:
            ai_diag = outputs["api_diagnostic"].answer
            evidence = outputs["api_diagnostic"].evidence
        elif "billing" in outputs:
            ai_diag = outputs["billing"].answer
            evidence = outputs["billing"].evidence
        async with stages.stage("ticket", "正在创建工单并转人工") as st:
            with timer() as t:
                tk = await ticket.create_from_context(
                    query=state["query"], intent=state["intent"], entities=state.get("entities", {}),
                    ai_diagnosis=ai_diag, evidence=evidence, ctx=ctx)
            trace.step("ticket", input_summary=state["query"], output_summary=tk.message, duration_ms=t.ms)
            st.done(f"工单 {tk.ticket_id} 已创建")
            return {"ticket_id": tk.ticket_id, "ticket_message": tk.message}

    async def summarize_node(state: AgentState) -> dict:
        outputs = state.get("agent_outputs", {})
        cards = []
        for name in ("api_diagnostic", "billing", "doc_rag"):
            o = outputs.get(name)
            if o is not None and getattr(o, "card", None):
                cards.append(o.card)
        general = outputs.get("general")

        card = None
        if general and not cards:
            draft = general
        elif len(cards) == 1:
            card = cards[0]
            draft = render_card(card)
        elif len(cards) > 1:
            # 复合问题（如 429）：小模型合并多条结论，证据/步骤取并集
            # 只有这一支真的调模型，单卡片/闲聊分支是纯拼装，发阶段只会闪一下
            async with stages.stage("summarize", "正在归纳多条结论") as st:
                with timer() as t:
                    gen = await client.chat(
                        [
                            {"role": "system", "content": "把以下多条结论合并成一句连贯、无重复、结论先行的中文结论，只输出结论本身。"},
                            {"role": "user", "content": "\n".join(c["conclusion"] for c in cards)},
                        ],
                        model=model_for("summarize"), temperature=0.2)
                st.done("已归纳结论")
            trace.step("summarize", output_summary=gen.content[:160], duration_ms=t.ms, token_usage=gen.total_tokens)
            card = {
                "conclusion": gen.content.strip(),
                "evidence": [e for c in cards for e in c["evidence"]],
                "steps": [s for c in cards for s in c["steps"]],
            }
            draft = render_card(card)
        else:
            draft = "这个问题我先帮你转接人工技术支持，请稍候。"
        return {"draft_answer": draft, "card": card}

    async def security_node(state: AgentState) -> dict:
        from app.guardrail import desensitize
        async with stages.stage("security", "正在做安全与脱敏审查") as st:
            with timer() as t:
                res = security.review_output(state.get("draft_answer", ""))
                card = state.get("card")
                clean_card = desensitize.desensitize_obj(card) if card else None
            trace.step("security", output_summary=f"脱敏类型={res.sensitive_found}", duration_ms=t.ms)
            # 命中了哪几类敏感信息不外泄，只告知已处理
            st.done("已脱敏并通过审查" if res.sensitive_found else "审查通过")
            return {"final_answer": res.clean_text, "card": clean_card}

    g = StateGraph(AgentState)
    g.add_node("load_context", load_context)
    g.add_node("intent", intent_node)
    g.add_node("clarify", clarify_node)
    g.add_node("semantic_cache", semantic_cache_node)
    g.add_node("specialists", specialists_node)
    g.add_node("ticket", ticket_node)
    g.add_node("summarize", summarize_node)
    g.add_node("security", security_node)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "intent")
    g.add_conditional_edges("intent", after_intent, {
        "clarify": "clarify",
        "semantic_cache": "semantic_cache",
        "specialists": "specialists",
    })
    g.add_edge("clarify", END)
    g.add_conditional_edges("semantic_cache", after_semantic_cache, {"cached": END, "miss": "specialists"})
    g.add_edge("specialists", "ticket")
    g.add_edge("ticket", "summarize")
    g.add_edge("summarize", "security")
    g.add_edge("security", END)
    return g.compile()


async def run(
    *,
    query: str,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    is_internal: bool = False,
    on_stage: stage.StageCallback | None = None,
) -> dict:
    """执行一次完整编排，返回结果并持久化链路与记忆。

    `on_stage` 可选：传入则在各节点开始/结束时回调，供调用方实时推给前端。
    不传时阶段发射器是空操作，行为与改造前完全一致。
    """
    trace = TraceCollector(tenant_id=tenant_id, conversation_id=conversation_id)
    stages = stage.StageEmitter(on_stage=on_stage)
    # 身份由服务端（已鉴权的调用方）注入，工具层不从模型输出里取
    ctx = ToolContext(
        tenant_id=tenant_id,
        trace_id=trace.trace_id,
        is_internal=is_internal,
        user_id=user_id,
        conversation_id=conversation_id,
        on_stage=stages.accept,
    )

    graph = build_graph(ctx, trace, stages)

    init: AgentState = {
        "tenant_id": tenant_id, "user_id": user_id, "conversation_id": conversation_id,
        "is_internal": is_internal, "query": query,
    }
    try:
        final = await graph.ainvoke(init)
    except Exception as e:  # noqa: BLE001  管线级兜底：规则回复 + 自动建单转人工
        trace.step("fallback", input_summary=query, status="error", error=f"{type(e).__name__}: {e}")
        await stages.emit("fallback", "处理中遇到异常，正在转人工", "running")
        tk = await ticket.create_from_context(
            query=query, intent="ticket", entities={}, ai_diagnosis="",
            evidence={"pipeline_error": f"{type(e).__name__}: {e}"}, ctx=ctx)
        await stages.emit("fallback", f"工单 {tk.ticket_id} 已创建", "success")
        await trace.persist()
        return {
            "answer": f"{fallback.rule_reply(None)}（工单号 {tk.ticket_id}）",
            "intent": None, "confidence": 0.0, "citations": [], "need_human": True,
            "ticket_id": tk.ticket_id, "trace_id": trace.trace_id,
            "total_tokens": trace.total_tokens, "entities": {}, "need_clarify": False,
        }

    # 建单友好提示统一在此拼接（最终状态稳定含 ticket_message，避免节点可见性时序问题）
    answer = final.get("final_answer", "")
    ticket_message = final.get("ticket_message")
    if ticket_message and ticket_message not in answer:
        answer = f"{answer}\n\n{ticket_message}"

    result = {
        "answer": answer,
        "intent": final.get("intent"),
        "confidence": final.get("confidence"),
        "citations": final.get("rag_citations", []),
        "card": final.get("card"),
        "need_human": final.get("need_human", False),
        "ticket_id": final.get("ticket_id"),
        "trace_id": trace.trace_id,
        "total_tokens": trace.total_tokens,
        "entities": final.get("entities", {}),
        "need_clarify": final.get("need_clarify", False),
        "from_cache": final.get("from_cache", False),
    }

    # 记忆：写入本轮对话
    await session.append_message(conversation_id, "user", query)
    await session.append_message(conversation_id, "assistant", result["answer"])
    # 实体与意图落库：conversation 的这两列建表就有，此前从未被写入，
    # 导致 Redis 一过期就再也拿不回来
    await mem_store.save_conversation_state(
        conversation_id, entities=result["entities"], intent=result["intent"]
    )
    # 跨会话画像：只记结构化派生事实，入库前已脱敏
    await user_profile.remember(tenant_id, user_id, result["entities"])
    # 历史超窗则把最旧部分压成摘要，此前是直接 ltrim 丢弃
    await compact.maybe_compact(conversation_id)
    # 持久化链路
    await trace.persist()
    # 成本统计
    await cost.record(tenant_id, conversation_id, "mixed", trace.total_tokens)
    # 语义缓存：仅缓存通用文档问答结果
    if (
        final.get("intent") == "doc_qa"
        and not final.get("need_clarify")
        and not final.get("need_human")
        and not final.get("history")
        and not result["from_cache"]
        and result["answer"]
        and final.get("query_embedding") is not None
    ):
        await semantic_cache.put(tenant_id, query, result, final["query_embedding"])

    return result
