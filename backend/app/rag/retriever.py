# @author: liuqinhe
"""混合检索：向量检索 + BM25 关键词检索 → RRF 融合。

错误码类问题支持对 error_code 标量字段精确过滤召回。

租户隔离在这里有两处，都容易漏：
1. BM25 语料是全局的，必须在取 top-k **之前**按租户掩码
2. RRF 去重 key 必须带租户，否则两租户同名内容会互相覆盖元信息
"""

import time

import jieba
from rank_bm25 import BM25Okapi

from app.config import settings
from app.llm import client
from app.rag import store

_bm25: BM25Okapi | None = None
_corpus: list[dict] = []
_built_at: float = 0.0


def _tokenize(text: str) -> list[str]:
    return [t for t in jieba.lcut(text.lower()) if t.strip()]


def _ensure_bm25() -> None:
    """懒加载并缓存 BM25 索引（基于 Milvus 中全部切片）。

    除显式 reset 外，索引超过 bm25_ttl_seconds 也会重建。TTL 是为多 worker
    部署兜底：BM25 是进程内状态，worker A 处理完的上传，worker B 的索引不会
    知道，靠 reset 通知不到。代价是最坏情况下新文档延迟一个 TTL 才可被关键词命中。
    """
    global _bm25, _corpus, _built_at
    fresh = _bm25 is not None and (time.monotonic() - _built_at) <= settings.bm25_ttl_seconds
    if fresh:
        return
    _corpus = store.all_chunks()
    _bm25 = BM25Okapi([_tokenize(c["content"]) for c in _corpus]) if _corpus else None
    _built_at = time.monotonic()


def reset_bm25() -> None:
    """ingest 与上传处理完成后调用，强制重建 BM25。"""
    global _bm25, _corpus, _built_at
    _bm25, _corpus, _built_at = None, [], 0.0


def _rrf_fuse(rankings: list[list[tuple[str, str]]], k: int = 60) -> dict[tuple[str, str], float]:
    """Reciprocal Rank Fusion：输入多个有序 id 列表，输出融合分。"""
    scores: dict[tuple[str, str], float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _key(hit: dict) -> tuple[str, str]:
    """融合与去重的 key。必须带租户：两个租户可能有完全相同的 content，
    只用 content 作 key 会让元信息互相覆盖、引用张冠李戴。"""
    return (hit.get("tenant_id", ""), hit["content"])


async def hybrid_search(
    query: str, *, tenant_id: str, top_k_each: int = 20, error_code: str | None = None
) -> tuple[list[dict], float]:
    """混合召回，返回 (候选片段, 最高向量余弦)。向量余弦用于更稳健的无命中判定。

    tenant_id 必填、无默认值：漏传时直接 TypeError，而不是静默跨租户召回。
    """
    _ensure_bm25()

    # 1) 向量检索（过滤表达式由 store 内部按租户拼装）
    qvec = await client.embed_one(query)
    vec_hits = store.search(qvec, tenant_id=tenant_id, top_k=top_k_each, error_code=error_code)
    # 错误码精确过滤若无结果，放开错误码条件重试。
    # 注意放开的只是错误码，租户条件绝不放开。
    if error_code and not vec_hits:
        vec_hits = store.search(qvec, tenant_id=tenant_id, top_k=top_k_each)

    # 2) BM25 关键词检索
    bm25_hits: list[dict] = []
    if _bm25 is not None and _corpus:
        scores = _bm25.get_scores(_tokenize(query))
        # 先按租户把不可见条目置为负无穷，再取 top-k。顺序很关键：
        # 先取 top-k 再过滤会白白损失召回——本租户的片段可能被其他租户的
        # 高分片段挤出候选，表现为"我自己上传的文档搜不到"。
        visible = ("", tenant_id) if tenant_id else ("",)
        masked = [
            s if _corpus[i].get("tenant_id", "") in visible else float("-inf")
            for i, s in enumerate(scores)
        ]
        top_idx = sorted(range(len(masked)), key=lambda i: masked[i], reverse=True)[:top_k_each]
        bm25_hits = [_corpus[i] for i in top_idx if masked[i] > 0]

    # vec_hits 在前，同片段优先保留向量检索的元信息（含 score）
    by_key: dict[tuple[str, str], dict] = {}
    for h in vec_hits + bm25_hits:
        by_key.setdefault(_key(h), h)

    rrf = _rrf_fuse([[_key(h) for h in vec_hits], [_key(h) for h in bm25_hits]])

    candidates = []
    for key, score in sorted(rrf.items(), key=lambda x: x[1], reverse=True):
        item = dict(by_key[key])
        item["rrf_score"] = score
        candidates.append(item)

    top_vec = max((h["score"] for h in vec_hits), default=0.0)
    return candidates, top_vec
