"""公开元信息：不需要任何鉴权，前端登录前可探测注册开关、品牌、版本等。"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.services import settings as settings_svc

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
async def info() -> dict:
    return {
        "service": "FireworkRouter",
        "version": __version__,
        "openai_compatible_base_url": "/v1",
        "admin_base_url": "/admin",
        "auth_mode": "single_password",
        "scheduler_strategies": [
            "weighted_random", "round_robin", "priority",
            "least_used", "most_balance", "session_sticky", "fill_first",
        ],
    }
