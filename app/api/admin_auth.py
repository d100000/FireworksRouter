"""管理端单密码登录。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.services.session import create_session_token, verify_password

settings = get_settings()

router = APIRouter(prefix="/admin/auth", tags=["admin/auth"])


class LoginIn(BaseModel):
    password: str


class LoginOut(BaseModel):
    session_token: str
    expires_in: int


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn) -> LoginOut:
    if not verify_password(payload.password, settings.admin_password_hash):
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
    """无状态 JWT：前端清掉 localStorage 即可，这里仅做语义对称。"""
    return {"ok": True}
