# @author: liuqinhe
"""混合检索的租户隔离 —— 本子项目最重要的一组测试。

泄漏有三条独立路径，向量过滤只堵住一条。这里覆盖另外两条：
BM25 关键词召回、RRF 融合去重 key。
"""

import time

import pytest

from app.config import settings
from app.rag import retriever


def _chunk(content, tenant_id="", doc_id="d", source="builtin"):
    return {
        "content": content,
        "doc_title": "标题",
        "section": "章节",
        "category": "接入",
        "error_code": "",
        "version": "v1",
        "tenant_id": tenant_id,
        "doc_id": doc_id,
        "source": source,
    }


@pytest.fixture(autouse=True)
def _clean():
    """每个测试从干净的索引状态开始，模块级全局不能串味。"""
    retriever.reset_bm25()
    yield
    retriever.reset_bm25()


@pytest.fixture
def no_vec(monkeypatch):
    """屏蔽向量检索，单独观察 BM25 这条路径。"""
    async def _embed_one(text):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(retriever.client, "embed_one", _embed_one)
    monkeypatch.setattr(retriever.store, "search", lambda *a, **kw: [])


# BM25 的 IDF 会把"出现在每一篇文档里的词"判为无区分度（分数 ≤ 0）。
# 这是 BM25 的正常行为，生产语料有几百个切片不会遇到；测试里必须垫上
# 填充文档，否则测的是 IDF 退化而不是租户隔离。
#
# 填充文本与查询词元必须完全不相交：jieba 会把查询切成多个词，
# 只要有一个词重叠，填充文档就会被召回、干扰断言。
# 因此下面统一用无意义词 QUERY_TOKEN 作为唯一查询词。
QUERY_TOKEN = "zzqq"
_FILLER = [
    "接入指南注册实名流程",
    "账单费用构成超额计费",
    "回调重试策略验签",
    "数据来源更新周期",
    "常见问题速查联系方式",
]


def _load_corpus(monkeypatch, chunks, *, filler=True):
    corpus = list(chunks)
    if filler:
        corpus += [_chunk(t) for t in _FILLER]
    monkeypatch.setattr(retriever.store, "all_chunks", lambda *a, **kw: corpus)


# ---------- BM25 租户隔离 ----------

async def test_bm25不会召回其他租户的片段(monkeypatch, no_vec):
    _load_corpus(monkeypatch, [
        _chunk(f"内部结算通道 {QUERY_TOKEN} 专用", tenant_id="t2", source="upload"),
    ])
    got, _ = await retriever.hybrid_search(QUERY_TOKEN, tenant_id="t1")
    assert got == []


async def test_本租户能召回自己的片段(monkeypatch, no_vec):
    _load_corpus(monkeypatch, [
        _chunk(f"内部结算通道 {QUERY_TOKEN} 专用", tenant_id="t1", source="upload"),
    ])
    got, _ = await retriever.hybrid_search(QUERY_TOKEN, tenant_id="t1")
    assert len(got) == 1 and got[0]["tenant_id"] == "t1"


async def test_bm25能召回全局内置文档(monkeypatch, no_vec):
    """tenant_id 为空的内置文档对所有租户可见。"""
    _load_corpus(monkeypatch, [_chunk(f"签名算法 {QUERY_TOKEN} 五步")])
    got, _ = await retriever.hybrid_search(QUERY_TOKEN, tenant_id="t1")
    assert len(got) == 1 and got[0]["tenant_id"] == ""


async def test_空租户只能看到全局(monkeypatch, no_vec):
    _load_corpus(monkeypatch, [
        _chunk(f"全局 {QUERY_TOKEN} 内容", tenant_id=""),
        _chunk(f"私有 {QUERY_TOKEN} 内容", tenant_id="t1", source="upload"),
    ])
    got, _ = await retriever.hybrid_search(QUERY_TOKEN, tenant_id="")
    assert [c["tenant_id"] for c in got] == [""]


# ---------- 掩码顺序：先掩码再取 top-k ----------

class _FakeBM25:
    """给定分数数组，绕开 BM25 的 IDF 细节，精确验证掩码与取 top-k 的顺序。"""

    def __init__(self, scores):
        self._scores = scores

    def get_scores(self, tokens):
        return list(self._scores)


async def test_掩码发生在取topk之前(monkeypatch, no_vec):
    """30 条高分属于 t2，1 条低分属于 t1，top_k_each=20。

    若先取 top-k 再按租户过滤，t1 那条排在第 31 位、会被挤出候选，结果为空。
    先掩码再取 top-k 才能召回它。这是本设计里最容易写错的一处。
    """
    corpus = [_chunk(f"别人的高分内容{i}", tenant_id="t2", source="upload") for i in range(30)]
    corpus.append(_chunk("我的低分内容", tenant_id="t1", source="upload"))
    _load_corpus(monkeypatch, corpus)
    monkeypatch.setattr(retriever, "_bm25", _FakeBM25([10.0] * 30 + [0.5]))
    monkeypatch.setattr(retriever, "_corpus", corpus)
    monkeypatch.setattr(retriever, "_built_at", time.monotonic())

    got, _ = await retriever.hybrid_search("查询", tenant_id="t1", top_k_each=20)
    assert [c["content"] for c in got] == ["我的低分内容"]


async def test_零分片段不进候选(monkeypatch, no_vec):
    corpus = [_chunk("无关内容", tenant_id="t1", source="upload")]
    _load_corpus(monkeypatch, corpus)
    monkeypatch.setattr(retriever, "_bm25", _FakeBM25([0.0]))
    monkeypatch.setattr(retriever, "_corpus", corpus)
    monkeypatch.setattr(retriever, "_built_at", time.monotonic())
    got, _ = await retriever.hybrid_search("查询", tenant_id="t1")
    assert got == []


# ---------- 融合去重 key ----------

async def test_去重key带租户不跨租户覆盖(monkeypatch):
    """两租户有完全相同的 content 时，元信息不能互相覆盖。

    原实现用 content 本身作 key，会导致引用张冠李戴。
    """
    same = f"{QUERY_TOKEN} 通道配置"

    async def _embed_one(text):
        return [0.1]

    def _search(qvec, *, tenant_id, top_k=20, error_code=None):
        # 向量侧只返回本租户那条（store 已按租户过滤）
        return [{**_chunk(same, tenant_id="t1", doc_id="mine", source="upload"), "score": 0.9}]

    monkeypatch.setattr(retriever.client, "embed_one", _embed_one)
    monkeypatch.setattr(retriever.store, "search", _search)
    # BM25 语料里同时存在两个租户的同名内容
    _load_corpus(monkeypatch, [
        _chunk(same, tenant_id="t1", doc_id="mine", source="upload"),
        _chunk(same, tenant_id="t2", doc_id="theirs", source="upload"),
    ])

    got, _ = await retriever.hybrid_search(QUERY_TOKEN, tenant_id="t1")
    assert got, "本租户内容应被召回"
    assert all(c["tenant_id"] == "t1" for c in got)
    assert all(c["doc_id"] == "mine" for c in got), "元信息被其他租户覆盖了"


# ---------- 向量侧透传 ----------

async def test_租户与错误码透传给store(monkeypatch):
    calls = []

    async def _embed_one(text):
        return [0.1]

    def _search(qvec, *, tenant_id, top_k=20, error_code=None):
        calls.append({"tenant_id": tenant_id, "error_code": error_code})
        return []

    monkeypatch.setattr(retriever.client, "embed_one", _embed_one)
    monkeypatch.setattr(retriever.store, "search", _search)
    _load_corpus(monkeypatch, [])
    await retriever.hybrid_search("查询", tenant_id="t9", error_code="SIGN_INVALID")
    # 断言首次调用：错误码无结果会触发第二次放开错误码的调用
    assert calls[0] == {"tenant_id": "t9", "error_code": "SIGN_INVALID"}


async def test_错误码无结果时退回仍带租户过滤(monkeypatch):
    """退回的是错误码条件，不是租户条件。"""
    calls = []

    async def _embed_one(text):
        return [0.1]

    def _search(qvec, *, tenant_id, top_k=20, error_code=None):
        calls.append({"tenant_id": tenant_id, "error_code": error_code})
        return []

    monkeypatch.setattr(retriever.client, "embed_one", _embed_one)
    monkeypatch.setattr(retriever.store, "search", _search)
    _load_corpus(monkeypatch, [])
    await retriever.hybrid_search("查询", tenant_id="t9", error_code="X")
    assert len(calls) == 2
    assert calls[1]["error_code"] is None      # 错误码条件被放开
    assert calls[1]["tenant_id"] == "t9"       # 租户条件绝不放开


async def test_tenant_id不可省略(monkeypatch, no_vec):
    _load_corpus(monkeypatch, [])
    with pytest.raises(TypeError):
        await retriever.hybrid_search("查询")


# ---------- 索引新鲜度 ----------

async def test_显式reset后重建索引(monkeypatch, no_vec):
    builds = []

    def _all(*a, **kw):
        builds.append(1)
        return [_chunk("内容 zzqq")]

    monkeypatch.setattr(retriever.store, "all_chunks", _all)
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    assert len(builds) == 1, "同一索引不该反复重建"
    retriever.reset_bm25()
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    assert len(builds) == 2


async def test_TTL到期后自动重建(monkeypatch, no_vec):
    """BM25 是进程内状态：多 worker 部署时 worker A 处理的上传，
    worker B 不会知道。TTL 让陈旧自愈。"""
    builds = []

    def _all(*a, **kw):
        builds.append(1)
        return [_chunk("内容 zzqq")]

    monkeypatch.setattr(retriever.store, "all_chunks", _all)
    monkeypatch.setattr(settings, "bm25_ttl_seconds", 60)
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    # 把构建时间推回到 TTL 之前
    monkeypatch.setattr(retriever, "_built_at", time.monotonic() - 61)
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    assert len(builds) == 2


async def test_TTL未到期不重建(monkeypatch, no_vec):
    """每次查询都重建是性能灾难。"""
    builds = []

    def _all(*a, **kw):
        builds.append(1)
        return [_chunk("内容 zzqq")]

    monkeypatch.setattr(retriever.store, "all_chunks", _all)
    monkeypatch.setattr(settings, "bm25_ttl_seconds", 60)
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    monkeypatch.setattr(retriever, "_built_at", time.monotonic() - 10)
    await retriever.hybrid_search("zzqq", tenant_id="t1")
    assert len(builds) == 1


async def test_空语料不报错(monkeypatch, no_vec):
    _load_corpus(monkeypatch, [])
    got, top = await retriever.hybrid_search("查询", tenant_id="t1")
    assert got == [] and top == 0.0
