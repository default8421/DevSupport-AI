# @author: liuqinhe
"""上下文压缩：在 token 预算内保留最相关片段，降低生成成本。

策略（无需额外 LLM 调用，低成本）：
1. 丢弃 rerank 分数低于阈值的片段（弱相关噪声）。
2. 按相关性从高到低累加，直到达到字符预算上限。
"""

DEFAULT_BUDGET_CHARS = 1800
MIN_RERANK_SCORE = 0.05


def compress(chunks: list[dict], budget_chars: int = DEFAULT_BUDGET_CHARS) -> list[dict]:
    """返回压缩后的片段列表（保序：按相关性）。"""
    kept, used = [], 0
    for c in chunks:
        if c.get("rerank_score", 1.0) < MIN_RERANK_SCORE:
            continue
        length = len(c["content"])
        if used + length > budget_chars and kept:
            break
        kept.append(c)
        used += length
    return kept


def build_context(chunks: list[dict]) -> tuple[str, list[dict]]:
    """拼接上下文文本并返回引用清单。

    用户上传的片段会额外标注来源。这些内容由外部提供却要拼进 prompt，
    标注让模型（和界面上的引用）能区分"平台官方文档"与"客户自己的材料"，
    在两者冲突时不至于把客户材料当成平台规则。

    这只是缓解，不是保证：提示注入没有可靠的纯提示层解法。
    真正限制影响面的是工具入参校验闸门与高风险工具对模型永久不可见。
    """
    blocks, citations = [], []
    for i, c in enumerate(chunks, 1):
        source = c.get("source", "builtin")  # 存量切片无此字段，视为内置
        origin = "（用户上传资料，非平台官方文档）" if source == "upload" else ""
        tag = f"[{i}] 《{c['doc_title']}》- {c['section']}{origin}"
        blocks.append(f"{tag}\n{c['content']}")
        citations.append(
            {
                "index": i,
                "doc_title": c["doc_title"],
                "section": c["section"],
                "version": c.get("version", "v1"),
                "score": round(c.get("rerank_score", 0.0), 3),
                "source": source,
            }
        )
    return "\n\n".join(blocks), citations
