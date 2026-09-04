# @author: liuqinhe
"""滚动摘要压缩：历史触达窗口上限时，把最旧的消息压成摘要。

此前超窗直接 ltrim 丢弃最旧消息，长对话必然失忆。

放在独立模块的原因：压缩要调 LLM，而 session.py 要保持纯 Redis、无外部依赖。
摘要由用户内容派生且会被重新注入 prompt，因此落库前必须脱敏。
"""

import logging

from app.config import settings
from app.guardrail import desensitize
from app.llm import client
from app.memory import session

log = logging.getLogger(__name__)

_SYS = (
    "把下面的技术支持对话压缩成一段不超过 200 字的中文摘要，"
    "保留用户身份特征、已确认的事实（错误码、接口、时间、已排除的原因）与未解决的问题，"
    "丢弃寒暄与重复内容。只输出摘要本身。"
)


async def maybe_compact(conv_id: str) -> bool:
    """历史达到窗口上限则压缩，返回是否执行了压缩。"""
    hist = await session.get_history(conv_id)
    if len(hist) < session.HISTORY_MAX:
        return False

    old, keep = hist[: -session.SUMMARY_KEEP], hist[-session.SUMMARY_KEEP :]
    if not old:
        return False

    previous = await session.get_summary(conv_id)
    body = "\n".join(f"{m['role']}: {m['content']}" for m in old)
    user_content = (f"已有摘要：{previous}\n\n" if previous else "") + f"新增对话：\n{body}"

    try:
        r = await client.chat(
            [{"role": "system", "content": _SYS}, {"role": "user", "content": user_content}],
            model=settings.llm_model_small,  # 压缩是简单任务，走小模型降本
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001  压缩是尽力而为的优化，失败不能拖垮整轮对话
        log.warning("会话 %s 摘要压缩失败，保持原历史: %s", conv_id, e)
        return False

    merged = desensitize.desensitize_text((r.content or "").strip())
    # 模型没给出有效摘要时保留原摘要（不覆盖成空），但窗口照收，
    # 否则每轮都会重试压缩、白烧 token
    if merged:
        await session.set_summary(conv_id, merged)
    await session.replace_history(conv_id, keep)
    return True
