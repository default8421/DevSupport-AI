# @author: liuqinhe
"""工具调用中心：注册、schema、超时、重试、脱敏日志、高风险隔离。

- 每个工具声明 input schema、超时、重试、是否高风险。
- execute() 真实执行并写 tool_call_log（参数与结果脱敏）。
- 高风险工具不暴露给 AI（openai_tools 默认排除），只能人工/后台执行。
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.config import settings
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import ToolCallLog

log = logging.getLogger(__name__)

# 面向用户的进行中文案。漏注册一个，前端就会露出英文函数名。
TOOL_LABELS = {
    "query_call_log": "正在查询调用日志",
    "query_recent_call_stats": "正在统计近期调用",
    "query_apikey_status": "正在核验 API Key 状态",
    "check_signature_canonical": "正在核对签名串",
    "query_plan": "正在查询套餐",
    "query_usage": "正在查询用量",
    "query_bill": "正在查询账单",
    "create_ticket": "正在创建工单",
    "query_ticket": "正在查询工单",
    "reset_api_key": "正在重置 API Key",
    "change_plan": "正在变更套餐",
    "refund": "正在处理退款",
}


def tool_label(name: str, *, done: bool = False) -> str:
    running = TOOL_LABELS.get(name, f"正在调用 {name}")
    if not done:
        return running
    return "已" + running[2:] if running.startswith("正在") else f"已完成 {name}"


@dataclass
class ToolContext:
    """工具执行上下文：身份一律由服务端拥有，不接受模型或调用方伪造。

    user_id / conversation_id 放在这里而不是 args 里，是为了让模型即使
    在工具循环中看到 create_ticket，也无法伪造他人身份建单。

    on_stage 可选：工具调用前后推一条阶段事件。它是展示通道，
    回调失败不得影响工具执行。
    """

    tenant_id: str
    trace_id: str = ""
    is_internal: bool = False
    user_id: str = ""
    conversation_id: str = ""
    on_stage: Callable[[dict], Awaitable[None]] | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema
    func: Callable[[dict, ToolContext], Awaitable[dict]]
    timeout: float = settings.tool_timeout_seconds
    retries: int = 1
    high_risk: bool = False
    category: str = "general"


REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    REGISTRY[spec.name] = spec
    return spec


def openai_tools(include_high_risk: bool = False) -> list[dict]:
    """返回可供 LLM function calling 的工具 schema（默认排除高风险）。"""
    tools = []
    for spec in REGISTRY.values():
        if spec.high_risk and not include_high_risk:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
        )
    return tools


def tools_for(names: list[str], include_high_risk: bool = False) -> list[dict]:
    """按名字挑出 LLM 可见的工具 schema。

    有界工具循环靠它把每个专家的可见范围压到 3-5 个，控制选错工具率。
    高风险工具恒定排除（include_high_risk 仅供内部后台用途）。
    """
    out = []
    for name in names:
        spec = REGISTRY.get(name)
        if spec is None or (spec.high_risk and not include_high_risk):
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
        )
    return out


_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_args(spec: ToolSpec, args: dict) -> str | None:
    """按 spec.parameters 做最小校验，返回错误描述；None 表示通过。

    这是模型输出与真实执行之间的确定性闸门：模型可以填错参数，
    但填错的参数不允许进入工具函数。故意不引入 jsonschema 依赖。
    """
    schema = spec.parameters or {}
    props = schema.get("properties") or {}
    if not props:
        return None  # 无 schema 声明的工具不校验

    required = schema.get("required") or []
    missing = [k for k in required if k not in args or args[k] in (None, "")]
    if missing:
        return f"缺少必需参数: {', '.join(missing)}"

    unknown = [k for k in args if k not in props]
    if unknown:
        # 不静默丢弃：静默会把模型的参数错误变成无声的错误答案
        return f"不支持的参数: {', '.join(unknown)}"

    for k, v in args.items():
        if v is None:
            continue
        declared = props.get(k, {}).get("type")
        py = _TYPE_MAP.get(declared)
        if py is None:
            continue
        # bool 是 int 的子类，若声明为数字必须显式排除
        if declared in ("integer", "number") and isinstance(v, bool):
            return f"参数 {k} 类型应为 {declared}，实际为 bool"
        if not isinstance(v, py):
            return f"参数 {k} 类型应为 {declared}，实际为 {type(v).__name__}"
    return None


async def _log_call(ctx, name, args, result, status, duration_ms, error):
    async with AsyncSessionLocal() as s:
        s.add(
            ToolCallLog(
                trace_id=ctx.trace_id,
                tenant_id=ctx.tenant_id,
                tool_name=name,
                args_summary=desensitize.desensitize_text(json.dumps(args, ensure_ascii=False))[:1000],
                result_summary=desensitize.desensitize_text(json.dumps(result, ensure_ascii=False, default=str))[:1500],
                status=status,
                duration_ms=duration_ms,
                error_message=error,
            )
        )
        await s.commit()


async def _emit_stage(
    ctx: ToolContext, name: str, status: str, *, duration_ms: int | None = None
) -> None:
    if ctx.on_stage is None:
        return
    evt: dict = {
        "key": name,
        "label": tool_label(name, done=status != "running"),
        "status": status,
    }
    if status == "error":
        evt["label"] = "调用失败"
    if duration_ms is not None:
        evt["duration_ms"] = duration_ms
    try:
        await ctx.on_stage(evt)
    except Exception:
        # 展示通道坏了不能连累工具本身
        log.debug("工具阶段事件推送失败，忽略：%s", name, exc_info=True)


async def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    """执行工具：高风险拦截 + 超时 + 重试 + 脱敏日志。"""
    spec = REGISTRY.get(name)
    if spec is None:
        return {"ok": False, "error": f"未知工具: {name}"}

    # 高风险工具不允许 AI 直接执行（仅内部显式调用且需标记）
    if spec.high_risk and not ctx.is_internal:
        return {"ok": False, "error": f"工具 {name} 为高风险操作，需人工处理，AI 不可直接执行"}

    # 确定性入参校验：模型填错参数不允许进入工具函数
    invalid = validate_args(spec, args)
    if invalid is not None:
        await _log_call(ctx, name, args, {}, "rejected", 0, invalid)
        return {"ok": False, "error": f"参数校验失败: {invalid}"}

    await _emit_stage(ctx, name, "running")
    start = time.perf_counter()
    last_err = None
    for attempt in range(spec.retries + 1):
        try:
            data = await asyncio.wait_for(spec.func(args, ctx), timeout=spec.timeout)
            duration = int((time.perf_counter() - start) * 1000)
            result = {"ok": True, "data": data}
            await _log_call(ctx, name, args, data, "ok", duration, None)
            await _emit_stage(ctx, name, "success", duration_ms=duration)
            return result
        except asyncio.TimeoutError:
            last_err = f"工具调用超时(>{spec.timeout}s)"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
    duration = int((time.perf_counter() - start) * 1000)
    await _log_call(ctx, name, args, {}, "error", duration, last_err)
    await _emit_stage(ctx, name, "error", duration_ms=duration)
    return {"ok": False, "error": last_err}


def load_tools() -> None:
    """导入各工具模块以触发注册。"""
    from app.tools import (  # noqa: F401
        apikey,
        billing_tools,
        logs,
        signature,
        ticket_tools,
    )
