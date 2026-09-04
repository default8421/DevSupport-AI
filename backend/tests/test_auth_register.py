# @author: liuqinhe
"""自助注册：新用户拿到独立租户，并可立刻登录。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.db import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models import Tenant, User
from app.security import verify_password


@pytest.fixture
def client(tmp_path):
    db_file = tmp_path / "auth.db"
    sync_eng = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_eng)

    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sess = async_sessionmaker(eng, expire_on_commit=False)

    async def _override_db():
        async with sess() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides.pop(get_current_user, None)
    c = TestClient(app)
    yield c, sess, sync_eng
    app.dependency_overrides.clear()


def test_注册成功返回令牌并创建独立租户(client):
    c, _, sync_eng = client
    r = c.post(
        "/api/auth/register",
        json={"username": "alice", "password": "secret123", "display_name": "Alice"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"]
    assert body["user"]["username"] == "alice"
    assert body["user"]["display_name"] == "Alice"
    assert body["user"]["role"] == "customer_dev"
    assert body["user"]["tenant_id"].startswith("t_")
    assert body["user"]["tenant_name"]

    with Session(sync_eng) as s:
        users = list(s.scalars(select(User)))
        tenants = list(s.scalars(select(Tenant)))
    assert len(users) == 1
    assert len(tenants) == 1
    assert users[0].tenant_id == tenants[0].id
    assert verify_password("secret123", users[0].password_hash)


def test_用户名重复拒绝(client):
    c, _, _ = client
    payload = {"username": "bob", "password": "secret123"}
    assert c.post("/api/auth/register", json=payload).status_code == 201
    r = c.post("/api/auth/register", json=payload)
    assert r.status_code == 409
    assert "用户名" in r.json()["detail"]


def test_密码过短拒绝(client):
    c, _, _ = client
    r = c.post("/api/auth/register", json={"username": "carol", "password": "123"})
    assert r.status_code == 422


def test_非法用户名拒绝(client):
    c, _, _ = client
    r = c.post("/api/auth/register", json={"username": "a b", "password": "secret123"})
    assert r.status_code == 422


def test_不能通过请求指定内部角色(client):
    c, _, sync_eng = client
    r = c.post(
        "/api/auth/register",
        json={"username": "eve", "password": "secret123", "role": "admin"},
    )
    assert r.status_code == 201
    with Session(sync_eng) as s:
        user = s.scalars(select(User)).one()
    assert user.role == "customer_dev"


def test_注册后可用同一密码登录(client):
    c, _, _ = client
    c.post("/api/auth/register", json={"username": "dave", "password": "secret123"})
    r = c.post("/api/auth/login", json={"username": "dave", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "dave"
