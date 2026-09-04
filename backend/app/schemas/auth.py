# @author: liuqinhe
"""认证相关 DTO。"""

import re

from pydantic import BaseModel, Field, field_validator

_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$")


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=72)
    display_name: str | None = Field(default=None, max_length=64)
    tenant_name: str | None = Field(default=None, max_length=128)

    @field_validator("username")
    @classmethod
    def username_ok(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME_RE.match(v):
            raise ValueError("用户名为 3-32 位，需以字母开头，仅含字母数字下划线")
        return v


class UserInfo(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: str
    tenant_id: str
    tenant_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
