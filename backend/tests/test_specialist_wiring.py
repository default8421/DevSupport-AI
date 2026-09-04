# @author: liuqinhe
"""专家 Agent 接入工具循环后的接线与契约。

对外结构必须保持不变：supervisor 用 getattr 读 answer/citations/tokens/
need_human/need_ticket，字段名一旦改动会静默降级而不是报错。
"""

from dataclasses import dataclass, field

import pytest

from app.agents import api_diagnostic, billing, doc_rag, tool_loop
from app.agents.tool_loop import ToolLoopResult
from app.llm.client import ChatResult
from app.tools.registry import ToolContext

CTX = ToolContext(tenant_id="t_test", trace_id="tr", user_id="u", conversation_id="c")

_CARD_JSON = (
    '{"conclusion":"Key 已过期","evidence":["401"],"steps":["重新生成"],"need_ticket":false}'
)


@dataclass
class _Doc:
    """doc_rag.answer 的返回替身。"""

    answer: str = "文档说明"
    citations: list = field(
        default_factory=lambda: [{"index": 1, "doc_title": "鉴权与签名", "section": "401"}]
    )
    tokens: int = 3


@pytest.fixture
def stub(monkeypatch):
    """打桩工具循环 / RAG / 组装用 LLM，只验证接线与结构，不连库不调模型。"""

    async def _loop(**kw):
        _loop.kw = kw
        return ToolLoopResult(
            content="证据收集完成：401 且 Key 已过期",
            invocations=[
                {
                    "name": "query_apikey_status",
                    "args": {"app_id": "app_1"},
                    "ok": True,
                    "duration_ms": 5,
                    "error": None,
                }
            ],
            tokens=11,
        )

    async def _doc(*a, **kw):
        _doc.kw = kw
        return _Doc()

    async def _chat(messages, **kw):
        return ChatResult(content=_CARD_JSON, total_tokens=9)

    # _error_doc_fast 会查 error_code 表，测试里不连库，让它落到 doc_rag 桩上
    async def _no_fast(error_code):
        return None

    _loop.doc = _doc  # 让用例能同时拿到工具循环与 RAG 的入参
    monkeypatch.setattr(tool_loop, "run", _loop)
    monkeypatch.setattr(doc_rag, "answer", _doc)
    monkeypatch.setattr(api_diagnostic, "_error_doc_fast", _no_fast)
    monkeypatch.setattr(api_diagnostic.client, "chat", _chat)
    monkeypatch.setattr(billing.client, "chat", _chat)
    return _loop


# ---------- 诊断专家 ----------

async def test_诊断专家只暴露自己的四个工具(stub):
    await api_diagnostic.diagnose("401 报错", {"error_code": "AUTH_KEY_EXPIRED"}, CTX)
    assert set(stub.kw["tool_names"]) == {
        "query_call_log",
        "query_apikey_status",
        "query_recent_call_stats",
        "check_signature_canonical",
    }


async def test_诊断专家不暴露高风险工具(stub):
    await api_diagnostic.diagnose("我要退款并重置密钥", {}, CTX)
    assert not {"reset_api_key", "change_plan", "refund"} & set(stub.kw["tool_names"])


async def test_诊断结果结构不变(stub):
    r = await api_diagnostic.diagnose("401 报错", {}, CTX)
    assert r.card["conclusion"] == "Key 已过期"
    assert r.answer and r.citations
    # tokens 必须把工具循环的消耗算进去，否则成本统计失真
    assert r.tokens == 9 + 3 + 11
    assert r.invocations and r.invocations[0]["name"] == "query_apikey_status"
    assert r.degraded is False


async def test_诊断把实体作为提示带给模型(stub):
    await api_diagnostic.diagnose("查一下", {"request_id": "req_abc"}, CTX)
    assert "req_abc" in stub.kw["query"]


async def test_限流意图会提示模型看调用统计(stub):
    await api_diagnostic.diagnose("429 了", {}, CTX, is_rate_limit=True)
    assert "调用统计" in stub.kw["query"]


def _loop_returning(invocations):
    async def _loop(**kw):
        _loop.kw = kw
        return ToolLoopResult(content="收集完成", invocations=invocations, tokens=1)

    return _loop


async def test_给了requestid但查不到日志强制转人工(monkeypatch, stub):
    """确定性兜底，不依赖 LLM 自觉置 need_ticket（卡片桩里它是 false）。"""
    monkeypatch.setattr(
        tool_loop,
        "run",
        _loop_returning(
            [
                {
                    "name": "query_call_log",
                    "args": {"request_id": "req_x"},
                    "ok": True,
                    "data": {"found": False},
                    "duration_ms": 1,
                    "error": None,
                }
            ]
        ),
    )
    r = await api_diagnostic.diagnose("查 req_x", {"request_id": "req_x"}, CTX)
    assert r.need_ticket is True


async def test_给了requestid但模型根本没查日志也转人工(monkeypatch, stub):
    monkeypatch.setattr(tool_loop, "run", _loop_returning([]))
    r = await api_diagnostic.diagnose("查 req_x", {"request_id": "req_x"}, CTX)
    assert r.need_ticket is True


async def test_日志查到了就不强制转人工(monkeypatch, stub):
    monkeypatch.setattr(
        tool_loop,
        "run",
        _loop_returning(
            [
                {
                    "name": "query_call_log",
                    "args": {"request_id": "req_x"},
                    "ok": True,
                    "data": {"found": True, "error_code": "SIGN_INVALID"},
                    "duration_ms": 1,
                    "error": None,
                }
            ]
        ),
    )
    r = await api_diagnostic.diagnose("查 req_x", {"request_id": "req_x"}, CTX)
    assert r.need_ticket is False


async def test_没给requestid时不触发该兜底(monkeypatch, stub):
    monkeypatch.setattr(tool_loop, "run", _loop_returning([]))
    r = await api_diagnostic.diagnose("签名怎么算", {}, CTX)
    assert r.need_ticket is False


async def test_诊断熔断时置need_ticket(monkeypatch, stub):
    async def _degraded(**kw):
        return ToolLoopResult(content="工具都失败了", invocations=[], tokens=1, degraded=True)

    monkeypatch.setattr(tool_loop, "run", _degraded)
    r = await api_diagnostic.diagnose("401 报错", {}, CTX)
    assert r.need_ticket is True and r.degraded is True


# ---------- 账单专家 ----------

async def test_账单专家只暴露三个计费工具(stub):
    await billing.handle("账单为什么涨了", {}, CTX)
    assert set(stub.kw["tool_names"]) == {"query_plan", "query_usage", "query_bill"}


async def test_账单结果结构不变(stub):
    r = await billing.handle("账单为什么涨了", {}, CTX)
    assert r.card["conclusion"] == "Key 已过期"
    assert r.answer and r.citations
    assert r.tokens == 9 + 3 + 11
    assert r.need_human is False


async def test_账单高风险关键词仍转人工(stub):
    r = await billing.handle("我要退款", {}, CTX)
    assert r.need_human is True


async def test_账单熔断时转人工(monkeypatch, stub):
    async def _degraded(**kw):
        return ToolLoopResult(content="工具都失败了", invocations=[], tokens=1, degraded=True)

    monkeypatch.setattr(tool_loop, "run", _degraded)
    r = await billing.handle("账单为什么涨了", {}, CTX)
    assert r.need_human is True and r.degraded is True


# ---------- 租户隔离：RAG 检索的租户只能来自服务端上下文 ----------

async def test_诊断把ctx租户传给rag(stub):
    await api_diagnostic.diagnose("401 报错", {}, CTX)
    assert stub.doc.kw["tenant_id"] == CTX.tenant_id


async def test_账单把ctx租户传给rag(stub):
    await billing.handle("账单为什么涨了", {}, CTX)
    assert stub.doc.kw["tenant_id"] == CTX.tenant_id


async def test_实体里的租户不被采信(stub):
    """租户身份只来自 ToolContext。若从实体取，用户就能在提问里伪造租户。"""
    await api_diagnostic.diagnose(
        "401 报错", {"tenant_id": "t_victim", "error_code": "X"}, CTX
    )
    assert stub.doc.kw["tenant_id"] == CTX.tenant_id != "t_victim"


async def test_账单把月份带给模型(stub):
    await billing.handle("8 月账单", {"month": "2026-08"}, CTX)
    assert "2026-08" in stub.kw["query"]
