# @author: liuqinhe
"""工具入参校验：模型输出与工具执行之间的确定性闸门。"""

import pytest

from app.tools.registry import ToolSpec, tools_for, validate_args


def _spec(**kw) -> ToolSpec:
    async def _noop(args, ctx):
        return {}

    base = {
        "name": "demo",
        "description": "演示工具",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "minutes": {"type": "integer"},
                "params": {"type": "object"},
            },
            "required": ["request_id"],
        },
        "func": _noop,
    }
    base.update(kw)
    return ToolSpec(**base)


def test_通过_合法入参():
    assert validate_args(_spec(), {"request_id": "req_1", "minutes": 60}) is None


@pytest.mark.parametrize("args", [{}, {"minutes": 60}, {"request_id": ""}, {"request_id": None}])
def test_拒绝_缺少必需参数(args):
    err = validate_args(_spec(), args)
    assert err is not None and "request_id" in err


def test_拒绝_未声明参数():
    err = validate_args(_spec(), {"request_id": "req_1", "drop_table": "x"})
    assert err is not None and "drop_table" in err


def test_拒绝_类型错误():
    err = validate_args(_spec(), {"request_id": "req_1", "minutes": "六十"})
    assert err is not None and "minutes" in err


def test_拒绝_布尔冒充整数():
    # isinstance(True, int) 为真，必须单独排除，否则模型传 true 会被当 1 放过
    err = validate_args(_spec(), {"request_id": "req_1", "minutes": True})
    assert err is not None and "minutes" in err


def test_接受_整数用于number字段():
    spec = _spec(parameters={
        "type": "object",
        "properties": {"score": {"type": "number"}},
        "required": [],
    })
    assert validate_args(spec, {"score": 1}) is None


def test_无schema时放行():
    assert validate_args(_spec(parameters={}), {"anything": 1}) is None


def test_建单入参不含身份字段():
    """user_id / conversation_id 必须由 ctx 提供，不能出现在 schema 里。

    否则一旦 create_ticket 在工具循环中对模型可见，模型就能伪造他人身份建单。
    """
    from app.tools import registry, ticket_tools  # noqa: F401  触发注册

    spec = registry.REGISTRY["create_ticket"]
    props = (spec.parameters or {}).get("properties", {})
    assert "user_id" not in props
    assert "conversation_id" not in props


def test_建单实参能过校验():
    """agents/ticket.py 组装的实参必须被 schema 接受，否则兜底建单会被闸门打断。"""
    from app.tools import registry, ticket_tools  # noqa: F401

    spec = registry.REGISTRY["create_ticket"]
    args = {
        "title": "401 报错",
        "category": "接入问题",
        "priority": "P2",
        "summary": "调用报 401",
        "related_request_ids": ["req_1"],
        "related_endpoint": "/v1/idcard/verify",
        "error_code": "SIGN_INVALID",
        "evidence": "{}",
        "ai_diagnosis": "签名错误",
    }
    assert validate_args(spec, args) is None


def test_toolcontext_默认身份为空():
    from app.tools.registry import ToolContext

    ctx = ToolContext(tenant_id="t1")
    assert ctx.user_id == "" and ctx.conversation_id == ""


def test_tools_for_按名字过滤且排除高风险():
    from app.tools import registry

    safe = _spec(name="safe_tool")
    risky = _spec(name="risky_tool", high_risk=True)
    registry.REGISTRY["safe_tool"] = safe
    registry.REGISTRY["risky_tool"] = risky
    try:
        out = tools_for(["safe_tool", "risky_tool", "不存在的工具"])
        names = [t["function"]["name"] for t in out]
        assert names == ["safe_tool"]
        assert out[0]["type"] == "function"
    finally:
        registry.REGISTRY.pop("safe_tool", None)
        registry.REGISTRY.pop("risky_tool", None)
