"""PLPT-24 / PLPT-25 / PLPT-59 / SEC-01: 5-Role RBAC Policy Matrix & FastAPI Guards."""

import os
from typing import Iterable

from fastapi import Header, HTTPException

from src.security.auth import (
    DEFAULT_SESSION_STORE,
    VALID_ROLES,
    AuthContext,
    resolve_auth_context,
)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "SALES_READER": {
        "queue:read",
        "explanations:read",
        "workspace:review",
        "workspace:override",
    },
    "SUPERVISOR": {
        "queue:read",
        "explanations:read",
        "workspace:review",
        "workspace:override",
        "workspace:block",
        "overdue:audit",
        "incident:hold",
    },
    "DATA_STEWARD": {
        "queue:read",
        "ingest:run",
        "quarantine:resolve",
        "dedup:review",
    },
    "MODEL_REVIEWER": {
        "queue:read",
        "evaluation:audit",
        "model:approve",
        "model:fallback",
        "monitoring:read",
    },
    "ADMINISTRATOR": {
        "queue:read",
        "explanations:read",
        "workspace:review",
        "workspace:override",
        "workspace:block",
        "incident:hold",
        "ops:backup",
        "ops:restore",
        "ops:exit_export",
        "rbac:manage",
    },
}


def has_permission(role: str, permission: str) -> bool:
    norm_role = str(role or "").strip().upper()
    return permission in ROLE_PERMISSIONS.get(norm_role, set())


def is_rbac_enforced_on_legacy() -> bool:
    """
    Mặc định True trên /api/queue và /api/block; có thể đặt RBAC_ENFORCE_LEGACY=false nếu cần tắt cho bộ test MVP cũ không truyền header auth.
    """
    return os.getenv("RBAC_ENFORCE_LEGACY", "true").strip().lower() in {"1", "true", "yes"}


def is_rbac_enforced_on_v1() -> bool:
    """Các endpoint mới /api/v1/workspace/* luôn bật RBAC 401/403 theo mặc định."""
    return os.getenv("RBAC_ENFORCE_V1", "true").strip().lower() in {"1", "true", "yes"}


def require_roles(
    allowed_roles: Iterable[str],
    enforce_flag_fn=is_rbac_enforced_on_v1,
    session_store=DEFAULT_SESSION_STORE,
):
    allowed_set = {r.upper() for r in allowed_roles}

    def _dependency(
        x_user_role: str | None = Header(default=None, alias="X-User-Role"),
        x_user_id: str | None = Header(default=None, alias="X-User-Id"),
        x_session_expired: str | None = Header(default=None, alias="X-Session-Expired"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AuthContext | None:
        enforce = enforce_flag_fn()
        from src.security.auth import get_env_mode

        if get_env_mode() == "production":
            enforce = True
        has_any_header = any([x_user_role, x_session_expired, authorization])
        if not enforce and not has_any_header:
            return None

        from src.services.queue_service import get_db_path

        ctx = resolve_auth_context(
            x_user_role=x_user_role,
            x_user_id=x_user_id,
            x_session_expired=x_session_expired,
            authorization=authorization,
            require_auth=True,
            session_store=session_store,
            db_path=get_db_path(),
        )
        if ctx is not None and ctx.role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"Vai trò {ctx.role} không có quyền thực hiện thao tác này.",
            )
        return ctx

    return _dependency


__all__ = [
    "AuthContext",
    "DEFAULT_SESSION_STORE",
    "ROLE_PERMISSIONS",
    "VALID_ROLES",
    "has_permission",
    "is_rbac_enforced_on_legacy",
    "is_rbac_enforced_on_v1",
    "require_roles",
    "resolve_auth_context",
]
