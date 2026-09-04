# @author: liuqinhe
"""编排阶段事件：让用户在等待时看见系统正在做什么。

原先的 SSE 是「编排全部跑完 → 再逐块吐答案」，用户按下发送后要盯着空白等数秒。
阶段事件把节点的开始/结束实时推给前端，仅用于展示。

两条约束：
1. 文案由后端给出——只有后端知道语义（具体走了哪个专家、有没有降级）。
2. 阶段事件坏掉不能连累编排。回调里的异常一律吞掉（见 `_fire`），
   否则前端的队列一满或断连就会把整条管线带崩。
"""

import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

StageCallback = Callable[[dict], Awaitable[None]]

# 意图到面向用户的中文说法
INTENT_LABELS = {
    "api_error": "接口报错",
    "rate_limit": "限流问题",
    "billing": "账单费用",
    "doc_qa": "文档用法",
    "data_quality": "数据质量",
    "ticket": "转人工",
    "chitchat": "闲聊",
}


@dataclass
class StageHandle:
    """让节点在结束时改写文案，例如把「正在识别问题类型」换成「已识别为接口报错」。"""

    label: str
    status: str = "success"

    def done(self, label: str, *, status: str = "success") -> None:
        self.label = label
        self.status = status


@dataclass
class StageEmitter:
    """把节点进度推给调用方。`on_stage` 为 None 时整体是空操作。"""

    on_stage: StageCallback | None = None
    _orders: dict[str, int] = field(default_factory=dict)

    def _order(self, key: str) -> int:
        # 同一 key 的 running 与终态共享序号，前端据此原地更新而不是追加一行
        if key not in self._orders:
            self._orders[key] = len(self._orders) + 1
        return self._orders[key]

    async def _fire(self, evt: dict) -> None:
        if self.on_stage is None:
            return
        try:
            await self.on_stage(evt)
        except Exception:
            # 展示用的东西不能反过来搞坏编排：断连让 queue.put 抛异常也只是少一个进度条
            log.debug("阶段事件推送失败，忽略：%s", evt.get("key"), exc_info=True)

    async def emit(
        self, key: str, label: str, status: str, duration_ms: int | None = None
    ) -> None:
        evt: dict = {"key": key, "label": label, "status": status, "order": self._order(key)}
        if duration_ms is not None:
            evt["duration_ms"] = duration_ms
        await self._fire(evt)

    async def accept(self, evt: dict) -> None:
        """给 ToolContext.on_stage 用：补上 order 再发出。

        工具层只知道自己的 key/label/status，序号由发射器统一分配，
        这样同一工具的 running 与终态仍共享一行。
        """
        await self.emit(evt["key"], evt["label"], evt["status"], evt.get("duration_ms"))

    @asynccontextmanager
    async def stage(self, key: str, label: str):
        """包住一个节点：进入发 running，正常退出发终态，异常发 error 后原样抛出。"""
        handle = StageHandle(label=label)
        await self.emit(key, label, "running")
        t0 = time.perf_counter()
        try:
            yield handle
        except Exception:
            # 只报「失败」，不把异常文本带到浏览器——它可能含内部细节
            await self.emit(key, "执行失败，已隔离", "error", int((time.perf_counter() - t0) * 1000))
            raise
        else:
            await self.emit(key, handle.label, handle.status, int((time.perf_counter() - t0) * 1000))
