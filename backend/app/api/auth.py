# @author: liuqinhe
"""认证接口：登录、注册、当前用户。"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.models import Tenant, User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserInfo
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_response(user: User, tenant: Tenant | None) -> TokenResponse:
    token = create_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role)
    return TokenResponse(
        access_token=token,
        user=UserInfo(
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            tenant_id=user.tenant_id,
            tenant_name=tenant.name if tenant else None,
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    # 用户不存在与密码错误统一返回同一提示，避免泄露账号是否存在
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one_or_none()
    return _token_response(user, tenant)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    exists = (
        await db.execute(select(User.id).where(User.username == body.username))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")

    display = (body.display_name or body.username).strip() or body.username
    tenant_name = (body.tenant_name or f"{display} 的空间").strip()
    tenant = Tenant(id=f"t_{uuid.uuid4().hex[:12]}", name=tenant_name, plan_id=None)
    user = User(
        id=f"u_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant.id,
        username=body.username,
        password_hash=hash_password(body.password),
        role="customer_dev",
        display_name=display,
    )
    db.add(tenant)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    await db.refresh(user)
    await db.refresh(tenant)
    return _token_response(user, tenant)


@router.get("/me", response_model=UserInfo)
async def me(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UserInfo:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one_or_none()
    return UserInfo(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant else None,
    )
