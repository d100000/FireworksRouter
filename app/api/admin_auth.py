"""管理端单密码登录 + 改密。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.config import get_settings
from app.services.session import (
    create_session_token,
    get_effective_password_hash,
    update_admin_password,
    verify_password,
)

settings = get_settings()

router = APIRouter(prefix="/admin/auth", tags=["admin/auth"])


class LoginIn(BaseModel):
    password: str


class LoginOut(BaseModel):
    session_token: str
    expires_in: int


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn) -> LoginOut:
    if not verify_password(payload.password, get_effective_password_hash()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid password", "type": "invalid_credentials"}},
        )
    return LoginOut(
        session_token=create_session_token(),
        expires_in=settings.session_token_ttl_hours * 3600,
    )


@router.post("/logout")
async def logout() -> dict[str, bool]:
    return {"ok": True}


@router.post("/change-password", dependencies=[Depends(require_admin)])
async def change_password(payload: ChangePasswordIn) -> dict[str, bool]:
    """已登录管理员修改密码：校验旧密码 → bcrypt 哈希新密码 → 写入 DB system_settings。

    下次登录起就用新密码。已签发的 session token 仍有效到原本的过期时间。
    """
    if not verify_password(payload.old_password, get_effective_password_hash()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"message": "旧密码错误", "type": "invalid_credentials"}},
        )
    if payload.old_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"message": "新密码不能与旧密码相同", "type": "invalid_request_error"}},
        )
    await update_admin_password(payload.new_password)
    return {"ok": True}
