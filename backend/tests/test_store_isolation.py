# @author: liuqinhe
"""向量库租户隔离：过滤表达式是整个隔离的咽喉，逐个 case 锁死。

全局内置文档 tenant_id 为空串，对所有租户可见；租户上传的文档只对本租户可见。
"""

import pytest

from app.rag import store

# ---------- 过滤表达式（纯函数，可离线断言） ----------

def test_只传租户():
    assert store.build_filter(tenant_id="t1") == 'tenant_id in ["", "t1"]'


def test_租户加错误码():
    got = store.build_filter(tenant_id="t1", error_code="SIGN_INVALID")
    assert got == 'tenant_id in ["", "t1"] and error_code == "SIGN_INVALID"'


def test_空租户只匹配全局文档():
    """失败到安全侧：租户为空时只能看到全局文档，而不是看到全部。"""
    assert store.build_filter(tenant_id="") == 'tenant_id in [""]'


def test_错误码为空时不加该条件():
    assert store.build_filter(tenant_id="t1", error_code=None) == 'tenant_id in ["", "t1"]'
    assert store.build_filter(tenant_id="t1", error_code="") == 'tenant_id in ["", "t1"]'


def test_租户id里的引号被转义():
    """租户 id 理论上来自 JWT，但不能依赖上游永远干净。"""
    got = store.build_filter(tenant_id='t" or tenant_id != "')
    # 转义后原始的注入片段不应作为有效表达式存在
    assert '\\"' in got
    assert got.count('tenant_id in') == 1
    assert not got.endswith('!= ""]')


def test_错误码里的引号被转义():
    got = store.build_filter(tenant_id="t1", error_code='X" or "1"=="1')
    assert '\\"' in got


def test_反斜杠被转义():
    got = store.build_filter(tenant_id="t\\1")
    assert "\\\\" in got


def test_tenant_id必须是关键字参数():
    with pytest.raises(TypeError):
        store.build_filter("t1")


def test_tenant_id不可省略():
    with pytest.raises(TypeError):
        store.build_filter()


# ---------- search ----------

class FakeClient:
    """记录传给 Milvus 的参数。"""

    def __init__(self, hits=None, rows=None):
        self.searches: list[dict] = []
        self.queries: list[dict] = []
        self.deletes: list[dict] = []
        self._hits = hits if hits is not None else []
        self._rows = rows if rows is not None else []
        self.flushed = 0

    def has_collection(self, name):
        return True

    def load_collection(self, name):
        return None

    def flush(self, name):
        self.flushed += 1

    def search(self, **kw):
        self.searches.append(kw)
        return [self._hits]

    def query(self, name, **kw):
        self.queries.append(kw)
        offset = kw.get("offset", 0)
        limit = kw.get("limit", 1000)
        return self._rows[offset : offset + limit]

    def delete(self, name, **kw):
        self.deletes.append(kw)
        return {"delete_count": 3}


def _hit(content, tenant_id="", score=0.9):
    return {
        "distance": score,
        "entity": {
            "content": content,
            "doc_title": "标题",
            "section": "章节",
            "category": "接入",
            "error_code": "",
            "version": "v1",
            "tenant_id": tenant_id,
            "doc_id": "doc_1",
            "source": "builtin",
        },
    }


@pytest.fixture
def fake(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(store, "get_client", lambda: c)
    return c


def test_search把租户过滤传给milvus(fake):
    store.search([0.1] * 4, tenant_id="t1")
    assert fake.searches[0]["filter"] == 'tenant_id in ["", "t1"]'


def test_search带错误码时组合过滤(fake):
    store.search([0.1] * 4, tenant_id="t1", error_code="AUTH_KEY_EXPIRED")
    assert fake.searches[0]["filter"] == (
        'tenant_id in ["", "t1"] and error_code == "AUTH_KEY_EXPIRED"'
    )


def test_search不再接受expr参数(fake):
    """调用方自己拼 expr 就意味着每个调用点都可能忘记租户条件。"""
    with pytest.raises(TypeError):
        store.search([0.1] * 4, tenant_id="t1", expr='error_code == "X"')


def test_search的tenant_id不可省略(fake):
    with pytest.raises(TypeError):
        store.search([0.1] * 4, top_k=5)


def test_search返回三个新字段(fake, monkeypatch):
    c = FakeClient(hits=[_hit("正文", tenant_id="t1")])
    monkeypatch.setattr(store, "get_client", lambda: c)
    out = store.search([0.1] * 4, tenant_id="t1")
    assert out[0]["tenant_id"] == "t1"
    assert out[0]["doc_id"] == "doc_1"
    assert out[0]["source"] == "builtin"


def test_output_fields含新字段(fake):
    store.search([0.1] * 4, tenant_id="t1")
    fields = fake.searches[0]["output_fields"]
    assert {"tenant_id", "doc_id", "source"} <= set(fields)


# ---------- all_chunks 分页 ----------

def test_all_chunks分页取全量(monkeypatch):
    rows = [{"content": f"c{i}", "tenant_id": "", "doc_id": "d", "source": "builtin"}
            for i in range(2500)]
    c = FakeClient(rows=rows)
    monkeypatch.setattr(store, "get_client", lambda: c)
    got = store.all_chunks(page_size=1000)
    assert len(got) == 2500  # 不能停在第一页
    assert len(c.queries) == 3  # 1000 + 1000 + 500


def test_all_chunks输出字段含租户(monkeypatch):
    c = FakeClient(rows=[])
    monkeypatch.setattr(store, "get_client", lambda: c)
    store.all_chunks()
    assert {"tenant_id", "doc_id", "source"} <= set(c.queries[0]["output_fields"])


def test_all_chunks达到上限时告警而非静默丢弃(monkeypatch, caplog):
    from app.config import settings

    monkeypatch.setattr(settings, "bm25_corpus_max", 1500)
    rows = [{"content": f"c{i}"} for i in range(5000)]
    c = FakeClient(rows=rows)
    monkeypatch.setattr(store, "get_client", lambda: c)
    with caplog.at_level("WARNING"):
        got = store.all_chunks(page_size=1000)
    assert len(got) <= 1500
    assert any("上限" in r.message for r in caplog.records)


def test_collection不存在时返回空(monkeypatch):
    class _NoColl(FakeClient):
        def has_collection(self, name):
            return False

    monkeypatch.setattr(store, "get_client", lambda: _NoColl())
    assert store.all_chunks() == []


# ---------- delete_by_doc ----------

def test_delete_by_doc按文档过滤(fake):
    store.delete_by_doc("doc_up_abc")
    assert fake.deletes[0]["filter"] == 'doc_id == "doc_up_abc"'


def test_delete_by_doc会flush(fake):
    """不 flush 的话删除未落盘，检索仍可能命中已删内容。"""
    store.delete_by_doc("doc_up_abc")
    assert fake.flushed >= 1


def test_delete_by_doc转义文档id(fake):
    store.delete_by_doc('d" or doc_id != "')
    assert '\\"' in fake.deletes[0]["filter"]
