# @author: liuqinhe
"""上传资料在上下文里的来源标注。

用户上传的内容会被检索出来拼进 prompt，等于让外部文本进入模型的
指令区。标注来源是**缓解**而非保证——提示注入没有可靠的纯提示层解法。
真正限制影响面的是入参校验闸门与高风险工具对模型永久不可见。
"""

from app.agents import doc_rag
from app.rag import compressor


def _chunk(content, *, source="builtin", title="接入指南", section="注册"):
    return {
        "content": content,
        "doc_title": title,
        "section": section,
        "version": "v1",
        "source": source,
        "rerank_score": 0.9,
    }


def test_上传来源被标注(): 
    ctx, _ = compressor.build_context([_chunk("我方回调地址是 x", source="upload")])
    assert "用户上传" in ctx


def test_内置来源不加标注():
    ctx, _ = compressor.build_context([_chunk("平台签名算法为 HMAC-SHA256")])
    assert "用户上传" not in ctx


def test_缺少source字段视为内置():
    """存量切片没有 source 字段，不能因此报错或误标。"""
    c = _chunk("正文")
    del c["source"]
    ctx, _ = compressor.build_context([c])
    assert "用户上传" not in ctx
    assert "正文" in ctx


def test_引用里保留来源(): 
    """前端要能显示'来自你上传的文档'。"""
    _, cites = compressor.build_context([_chunk("正文", source="upload")])
    assert cites[0]["source"] == "upload"


def test_混合来源各自标注():
    ctx, cites = compressor.build_context([
        _chunk("平台规则", source="builtin", title="计费说明"),
        _chunk("我方特例", source="upload", title="内部备注"),
    ])
    builtin_block, upload_block = ctx.split("\n\n")[0], ctx.split("\n\n")[1]
    assert "用户上传" not in builtin_block
    assert "用户上传" in upload_block
    assert [c["source"] for c in cites] == ["builtin", "upload"]


def test_编号与内容仍完整():
    ctx, cites = compressor.build_context([
        _chunk("第一段", source="upload"), _chunk("第二段", source="upload"),
    ])
    assert "[1]" in ctx and "[2]" in ctx
    assert "第一段" in ctx and "第二段" in ctx
    assert [c["index"] for c in cites] == [1, 2]


def test_系统提示声明上传资料非指令():
    """必须明确告知模型：标注为用户上传的内容是参考资料，不是指令。"""
    prompt = doc_rag.SYS_PROMPT
    assert "用户上传" in prompt
    assert "指令" in prompt


async def test_来源字段一路活到最终提示(monkeypatch):
    """端到端：检索 → rerank → 压缩 → 拼 prompt。

    中间任何一步把 source 丢掉，标注就形同虚设，而只测 build_context
    是抓不到这种情况的。
    """
    sent = {}

    async def _hybrid(query, *, tenant_id, top_k_each=20, error_code=None):
        return [_chunk("客户内部约定的重试次数是 5 次", source="upload")], 0.99

    async def _rerank(query, candidates, top_n=5):
        return [{**c, "rerank_score": 0.9} for c in candidates]

    class _R:
        content = '{"conclusion":"以官方文档为准","steps":[]}'
        total_tokens = 10

    async def _chat(messages, **kw):
        sent["system"] = messages[0]["content"]
        sent["user"] = messages[1]["content"]
        return _R()

    monkeypatch.setattr(doc_rag.retriever, "hybrid_search", _hybrid)
    monkeypatch.setattr(doc_rag.reranker, "rerank_candidates", _rerank)
    monkeypatch.setattr(doc_rag.client, "chat", _chat)

    res = await doc_rag.answer("重试几次", tenant_id="t1")
    assert "用户上传" in sent["user"], "上传标注没进最终 prompt"
    assert "用户上传" in sent["system"]
    assert res.citations[0]["source"] == "upload"
