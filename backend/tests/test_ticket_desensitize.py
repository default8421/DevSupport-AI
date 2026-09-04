# @author: liuqinhe
"""工单落库前的脱敏：工单是持久化边界，证据里可能带用户贴进对话的敏感信息。

security_node 只处理最终答复，且在建单之后才跑，指望不上。
"""

from app.agents import ticket
from app.tools.registry import ToolContext

CTX = ToolContext(tenant_id="t1", trace_id="tr", user_id="u1", conversation_id="c1")


async def _capture(monkeypatch) -> dict:
    """拦下 execute，拿到真正要落库的 args。"""
    seen = {}

    async def _exec(name, args, ctx):
        seen["name"], seen["args"], seen["ctx"] = name, args, ctx
        return {"ok": True, "data": {"ticket_id": "tk_1"}}

    monkeypatch.setattr(ticket, "execute", _exec)
    return seen


async def test_证据里的身份证被脱敏(monkeypatch):
    seen = await _capture(monkeypatch)
    # 工具循环会把模型填的参数记进证据，用户贴的身份证会随之进来
    evidence = {
        "tool_calls": [
            {
                "name": "check_signature_canonical",
                "ok": True,
                "args": {"params": {"idcard": "110101199001011234"}},
            }
        ]
    }
    await ticket.create_from_context(
        query="签名报错", intent="api_error", entities={}, ai_diagnosis="", evidence=evidence,
        ctx=CTX,
    )
    assert "110101199001011234" not in seen["args"]["evidence"]


async def test_证据里的密钥被脱敏(monkeypatch):
    seen = await _capture(monkeypatch)
    evidence = {"tool_findings": "用户的密钥是 ak_demo_abcdef1234567890abcdef12"}
    await ticket.create_from_context(
        query="报错", intent="api_error", entities={}, ai_diagnosis="", evidence=evidence, ctx=CTX
    )
    assert "ak_demo_abcdef1234567890abcdef12" not in seen["args"]["evidence"]


async def test_ai诊断里的手机号被脱敏(monkeypatch):
    seen = await _capture(monkeypatch)
    await ticket.create_from_context(
        query="报错", intent="api_error", entities={}, ai_diagnosis="联系人 13800138000",
        evidence={}, ctx=CTX,
    )
    assert "13800138000" not in seen["args"]["ai_diagnosis"]


async def test_身份不进args而走ctx(monkeypatch):
    seen = await _capture(monkeypatch)
    await ticket.create_from_context(
        query="报错", intent="api_error", entities={}, ai_diagnosis="", evidence={}, ctx=CTX
    )
    assert "user_id" not in seen["args"] and "conversation_id" not in seen["args"]
    assert seen["ctx"].user_id == "u1" and seen["ctx"].conversation_id == "c1"


async def test_正常内容不被破坏(monkeypatch):
    seen = await _capture(monkeypatch)
    await ticket.create_from_context(
        query="调用 /v1/idcard/verify 报 SIGN_INVALID", intent="api_error", entities={},
        ai_diagnosis="待签字符串参数顺序错误", evidence={"tool_findings": "错误码 SIGN_INVALID"},
        ctx=CTX,
    )
    assert "SIGN_INVALID" in seen["args"]["evidence"]
    assert seen["args"]["ai_diagnosis"] == "待签字符串参数顺序错误"
    assert seen["args"]["category"] == "API报错" and seen["args"]["priority"] == "P1"
