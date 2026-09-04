# @author: liuqinhe
"""内置文档浏览接口的路径约束。

doc_id 来自 URL 并被拼进 glob 模式。不约束字符集的话，
"../../x" 这类输入能让 glob 逃出知识库目录、读到磁盘上任意 *-*.md 文件。
"""

import pytest
from fastapi.testclient import TestClient

from app.deps import CurrentUser, get_current_user
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="u1", display_name="u1",
        role="customer_dev", tenant_id="t1",
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "doc_id",
    # 通配符与 ? 必须用百分号编码：裸 ? 会被当成查询串起点、根本到不了 doc_id
    ["..", "../../README", "..%2f..%2fREADME", "01/../..", "*", "%3F", "0[1]"],
)
def test_非字母数字的doc_id一律404(client, doc_id):
    r = client.get(f"/api/docs/{doc_id}")
    assert r.status_code == 404


def test_正常doc_id仍可读(client):
    r = client.get("/api/docs/01")
    assert r.status_code == 200
    assert r.json()["id"] == "01"
    assert r.json()["content"]


def test_不存在的数字id返回404(client):
    assert client.get("/api/docs/99").status_code == 404
