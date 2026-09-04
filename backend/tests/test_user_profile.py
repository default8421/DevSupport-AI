# @author: liuqinhe
"""跨会话用户画像：结构化派生事实，不存自由文本；严格租户隔离。"""

from app.memory import user_profile as up

# ---------- 事实派生 ----------

def test_派生_端点与错误码与appid():
    facts = up.derive_facts(
        {"endpoint": "/v1/idcard/verify", "error_code": "SIGN_INVALID", "app_id": "app_1"},
        plan_name=None,
    )
    assert ("endpoint", "/v1/idcard/verify") in facts
    assert ("recurring_error", "SIGN_INVALID") in facts
    assert ("app_id", "app_1") in facts


def test_request_id与状态码刻意不入库():
    """一次性标识长期留存没有复用价值，且会让表无限膨胀。"""
    assert up.derive_facts({"request_id": "req_abc123", "http_status": "401"}, None) == []


def test_套餐名来自专家而非实体():
    assert up.derive_facts({}, plan_name="专业版") == [("plan", "专业版")]


def test_空值与空白被忽略():
    assert up.derive_facts({"endpoint": "", "error_code": "   ", "app_id": None}, None) == []


def test_超长值被截断():
    facts = up.derive_facts({"endpoint": "/v1/" + "a" * 200}, None)
    assert len(facts[0][1]) <= up.MAX_VALUE_LEN


def test_派生值会脱敏():
    """身份证号不该被固化进长期记忆再回灌进 prompt。"""
    facts = up.derive_facts({"endpoint": "/v1/x?idcard=110101199001011234"}, None)
    assert "110101199001011234" not in facts[0][1]


def test_非字符串实体被忽略():
    assert up.derive_facts({"endpoint": 123, "error_code": ["a"]}, None) == []


# ---------- 注入提示行 ----------

async def test_无记录时为空串(monkeypatch):
    """空串而不是"该用户历史特征："这种畸形空提示。"""
    async def _fetch(tenant_id, user_id):
        return []

    monkeypatch.setattr(up, "_fetch_facts", _fetch)
    assert await up.profile_line("t", "u") == ""


async def test_拼接且限制条数(monkeypatch):
    rows = [
        {"kind": "endpoint", "value": "/v1/idcard/verify", "hits": 9},
        {"kind": "recurring_error", "value": "SIGN_INVALID", "hits": 3},
        {"kind": "plan", "value": "专业版", "hits": 1},
        {"kind": "app_id", "value": "app_1", "hits": 1},
        {"kind": "endpoint", "value": "/v1/ocr", "hits": 1},
        {"kind": "endpoint", "value": "/v1/face", "hits": 1},
    ]

    async def _fetch(tenant_id, user_id):
        return rows

    monkeypatch.setattr(up, "_fetch_facts", _fetch)
    line = await up.profile_line("t", "u")
    assert "该用户历史特征" in line
    assert "反复遇到 SIGN_INVALID（3 次）" in line
    assert "/v1/face" not in line  # 超过 MAX_FACTS 的不进来


async def test_出现一次的错误码不说反复(monkeypatch):
    """出现一次就说"反复"会误导诊断。"""
    async def _fetch(tenant_id, user_id):
        return [{"kind": "recurring_error", "value": "TIMEOUT", "hits": 1}]

    monkeypatch.setattr(up, "_fetch_facts", _fetch)
    line = await up.profile_line("t", "u")
    assert "反复" not in line and "TIMEOUT" in line


# ---------- 写入 ----------

async def test_无事实时不碰数据库(monkeypatch):
    def _boom():
        raise AssertionError("没有可记的事实时不应开事务")

    monkeypatch.setattr(up, "AsyncSessionLocal", _boom)
    await up.remember("t", "u", {}, None)


async def test_缺租户或用户时不写入(monkeypatch):
    """租户隔离是硬约束：身份不全宁可不记。"""
    def _boom():
        raise AssertionError("身份不全时不应写入")

    monkeypatch.setattr(up, "AsyncSessionLocal", _boom)
    await up.remember("", "u", {"endpoint": "/v1/x"}, None)
    await up.remember("t", "", {"endpoint": "/v1/x"}, None)


async def test_查询必须带租户条件(monkeypatch):
    """漏掉 tenant_id 会跨租户泄漏他人画像。"""
    captured = {}

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            captured["stmt"] = str(stmt)

            class _R:
                def all(self):
                    return []

            return _R()

    monkeypatch.setattr(up, "AsyncSessionLocal", lambda: _Sess())
    await up._fetch_facts("t1", "u1")
    assert "tenant_id" in captured["stmt"] and "user_id" in captured["stmt"]
