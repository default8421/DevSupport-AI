# @author: liuqinhe
"""待签字符串校验工具：不接触 Secret Key，校验签名的"构造过程"。

为什么不算 HMAC：api_key 表只存脱敏值，没有密钥字段；让用户把 Secret Key
贴进对话违反知识库的安全建议，且脱敏层本来就会把它打码。另外知识库里的
hmac_sha256() 伪代码只有一个入参，规格残缺，照它实现必然与真实平台不一致。

本工具覆盖知识库 SIGN_INVALID 四条排查要点中的三条（排序 / 时间戳 / 编码），
第二条（Secret 与 Key 是否配对）需要密钥，只能在输出里提示用户自查。
"""

import re
import time

from app.guardrail import desensitize
from app.tools.registry import ToolContext, ToolSpec, register

SECRET_PLACEHOLDER = "***"
TIMESTAMP_WINDOW_SECONDS = 300  # 知识库 02 节：偏差不超过 5 分钟

# 用户可能误贴进来的敏感键
_SECRET_LIKE = {"secret", "secret_key", "secretkey", "sign", "signature", "x-sign", "xsign"}
_PCT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def strip_secrets(params: dict) -> tuple[dict, list[dict]]:
    """剔除误贴的密钥/签名字段并告警。"""
    cleaned, leaked = {}, []
    for k, v in (params or {}).items():
        if str(k).strip().lower() in _SECRET_LIKE:
            leaked.append(str(k))
        else:
            cleaned[k] = v
    issues = []
    if leaked:
        issues.append(
            {
                "code": "SECRET_LEAKED_IN_PARAMS",
                "detail": f"参数里出现了不应参与拼接的字段：{', '.join(leaked)}",
                "fix": "待签字符串只含业务参数与 api_key、timestamp；签名本身不参与拼接。"
                "若刚才贴出了 Secret Key，请立即在控制台重置",
            }
        )
    return cleaned, issues


def build_canonical(params: dict, timestamp: str, api_key: str | None) -> tuple[str, list[str]]:
    """按知识库步骤 1-4 构造待签字符串；secret 用占位符，绝不接触真实密钥。"""
    merged = {str(k): "" if v is None else str(v) for k, v in (params or {}).items()}
    if api_key:
        merged["api_key"] = str(api_key)
    merged["timestamp"] = str(timestamp)
    keys = sorted(merged)  # 步骤 2：参数名 ASCII 升序
    joined = "&".join(f"{k}={merged[k]}" for k in keys)  # 步骤 3
    return f"{joined}&secret={SECRET_PLACEHOLDER}", keys  # 步骤 4


def check_timestamp(ts: str, now: int | None = None) -> list[dict]:
    """秒级 + 5 分钟窗口判定。"""
    raw = str(ts).strip()
    if not raw.isdigit():
        return [
            {
                "code": "TIMESTAMP_NOT_NUMERIC",
                "detail": f"时间戳不是纯数字：{raw[:32]}",
                "fix": "X-Timestamp 应为 10 位秒级 Unix 时间戳，不要用日期字符串",
            }
        ]
    if len(raw) == 13:
        return [
            {
                "code": "TIMESTAMP_UNIT",
                "detail": "时间戳为 13 位毫秒级",
                "fix": "改用 10 位秒级（毫秒值除以 1000 取整）",
            }
        ]
    if len(raw) != 10:
        return [
            {
                "code": "TIMESTAMP_LENGTH",
                "detail": f"时间戳长度为 {len(raw)} 位",
                "fix": "X-Timestamp 应为 10 位秒级 Unix 时间戳",
            }
        ]
    current = int(time.time()) if now is None else int(now)
    skew = abs(current - int(raw))
    if skew > TIMESTAMP_WINDOW_SECONDS:
        return [
            {
                "code": "TIMESTAMP_SKEW",
                "detail": f"与服务器时间相差 {skew} 秒，超出 {TIMESTAMP_WINDOW_SECONDS} 秒窗口",
                "fix": "校对服务器时区与 NTP 同步；签名请在发起请求时即时生成，不要复用旧时间戳",
            }
        ]
    return []


def check_encoding(params: dict) -> list[dict]:
    """检测被额外 URL 编码的 value（知识库排查要点 4）。"""
    hit = [
        str(k) for k, v in (params or {}).items() if isinstance(v, str) and _PCT_ESCAPE.search(v)
    ]
    if not hit:
        return []
    return [
        {
            "code": "VALUE_URL_ENCODED",
            "detail": f"这些参数的值出现了 %XX 转义：{', '.join(hit)}",
            "fix": "参与签名的 value 用 UTF-8 原文，不要先做 URL 编码；"
            "URL 编码只在最终发请求时施加",
        }
    ]


def _parse_pairs(s: str) -> list[tuple[str, str]]:
    out = []
    for seg in str(s).split("&"):
        if not seg:
            continue
        k, _, v = seg.partition("=")
        out.append((k.strip(), v))
    return out


def diff_canonical(expected_keys: list[str], client_string: str) -> list[dict]:
    """把用户自拼的串与正确构造对比，定位到第几个参数出错。"""
    issues: list[dict] = []
    pairs = _parse_pairs(client_string)
    if not pairs:
        return [
            {
                "code": "CLIENT_STRING_UNPARSEABLE",
                "detail": "无法从提供的字符串里解析出 key=value 结构",
                "fix": "确认格式为 key1=value1&key2=value2&...&secret=你的SecretKey",
            }
        ]

    if pairs[-1][0].lower() != "secret":
        issues.append(
            {
                "code": "SECRET_SUFFIX_MISSING",
                "detail": "末尾没有 &secret=",
                "fix": "按步骤 4，在拼接串末尾追加 &secret=你的SecretKey 之后再做哈希",
            }
        )
    client_keys = [k for k, _ in pairs if k.lower() != "secret"]

    missing = [k for k in expected_keys if k not in client_keys]
    if missing:
        issues.append(
            {
                "code": "PARAM_MISSING",
                "detail": f"待签串里缺少参数：{', '.join(missing)}",
                "fix": "所有业务参数加上 api_key、timestamp 都要参与拼接",
            }
        )
    extra = [k for k in client_keys if k not in expected_keys]
    if extra:
        issues.append(
            {
                "code": "PARAM_EXTRA",
                "detail": f"待签串里多了参数：{', '.join(extra)}",
                "fix": "只拼接实际发送的业务参数，不要加入调试字段或空值参数",
            }
        )

    # 只比较双方都有的参数的相对顺序，避免缺参/多参在这里重复报错
    common = [k for k in client_keys if k in expected_keys]
    target = [k for k in expected_keys if k in client_keys]
    if common != target:
        for i, (got, want) in enumerate(zip(common, target)):
            if got != want:
                issues.append(
                    {
                        "code": "ORDER_MISMATCH",
                        "detail": f"第 {i + 1} 个参数应为 {want}，实际为 {got}",
                        "fix": "按参数名 ASCII 升序排序后再拼接",
                    }
                )
                break
    return issues


async def check_signature_canonical(args: dict, ctx: ToolContext) -> dict:
    """校验待签字符串的构造过程，不做 HMAC 校验。"""
    params, issues = strip_secrets(args.get("params") or {})
    timestamp = args.get("timestamp", "")
    client_string = args.get("client_string")

    canonical, keys = build_canonical(params, timestamp, args.get("api_key"))
    issues += check_timestamp(timestamp)
    issues += check_encoding(params)

    if client_string:
        issues += diff_canonical(keys, client_string)
    else:
        issues.append(
            {
                "code": "NO_CLIENT_STRING",
                "detail": "用户未提供自己拼出的待签字符串，无法逐字符比对",
                "fix": "把你代码里拼出的待签字符串贴出来，可精确定位到第几个参数开始不一致",
            }
        )

    return {
        # 脱敏后返回：value 可能含身份证等 PII
        "canonical_string": desensitize.desensitize_text(canonical),
        "sorted_keys": keys,
        "issues": issues,
        "secret_checked": False,
        "note": "本工具未校验 HMAC 结果，不能据此断言签名正确；若构造无误仍失败，"
        "请核对 Secret Key 是否与该 API Key 配对、是否误用了测试环境密钥",
    }


register(
    ToolSpec(
        name="check_signature_canonical",
        description=(
            "校验 API 请求签名的待签字符串构造是否正确。检查参数字典序、是否缺少或多余参数、"
            "时间戳是否为秒级且在 5 分钟窗口内、value 是否被额外 URL 编码。"
            "不需要也不接受 Secret Key，因此无法判定签名最终值是否正确。"
            "适用于用户报 SIGN_INVALID / 401 签名错误的场景。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "params": {
                    "type": "object",
                    "description": "参与签名的业务参数键值对，不含签名本身",
                },
                "timestamp": {
                    "type": "string",
                    "description": "用户使用的 X-Timestamp 原值，原样传入不要修正",
                },
                "api_key": {"type": "string", "description": "X-Api-Key，可以是脱敏值"},
                "client_string": {
                    "type": "string",
                    "description": "用户自己拼出的待签字符串；有则可逐参数比对定位",
                },
            },
            "required": ["params", "timestamp"],
        },
        func=check_signature_canonical,
        category="diagnostic",
    )
)
