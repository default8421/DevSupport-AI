# @author: liuqinhe
"""闲聊与文档问答的边界：上传/想提问不能被当成「我只做 API」。"""

from app.agents import doc_rag, intent_router, supervisor


def test_能力询问走闲聊():
    assert intent_router.is_meta_chitchat("你能干嘛")
    assert intent_router.is_meta_chitchat("你会什么？")


def test_刚上传文档是元对话不是检索题():
    """「我上传文档了」要回引导，不能拿上传文档里「如何上传」的章节来答。"""
    assert intent_router.is_meta_chitchat("我上传文档了呀")
    assert intent_router.is_meta_chitchat("我已经上传了文档")


def test_只说想问某项目还没有具体问题():
    assert intent_router.is_meta_chitchat("我想提问关于interview项目的问题")
    assert intent_router.is_meta_chitchat("我想问一下 interview 项目")


def test_项目里的具体问题必须走文档问答():
    assert not intent_router.is_meta_chitchat("interview 项目的 RAG 怎么做的")
    assert not intent_router.is_meta_chitchat("签名算法怎么生成")
    assert not intent_router.is_meta_chitchat("Webhook 回调收不到怎么排查")


def test_闲聊提示允许回答上传知识库():
    prompt = supervisor.CHITCHAT_PROMPT
    assert "上传" in prompt
    assert "知识库" in prompt
    assert "API" in prompt


def test_文档问答提示以租户资料回答项目问题():
    prompt = doc_rag.SYS_PROMPT
    assert "用户上传" in prompt
    assert "指令" in prompt
    assert "以用户上传资料为准" in prompt
