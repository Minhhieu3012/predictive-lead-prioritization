"""PLPT-24 / PLPT-25 / SEC-01: 5-Role RBAC Policy Matrix & FastAPI Guards."""

import os
from typing import Iterable

from fastapi import Header, HTTPException

from src.security.auth import VALID_ROLES, AuthContext, resolve_auth_context

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
    "STAFF": {
        "queue:read",
        "explanations:read",
        "workspace:review",
        "workspace:override",
    },
    "PO_OPERATOR": {
        "queue:read",
        "explanations:read",
        "workspace:review",
        "workspace:override",
        "workspace:block",
        "incident:hold",
    },
}


def has_permission(role: str, permission: str) -> bool:
    norm_role = str(role or "").strip().upper()
    return permission in ROLE_PERMISSIONS.get(norm_role, set())


def is_rbac_enforced_on_legacy() -> bool:
    """
    Giữ cờ cấu hình để không làm gãy 35 unit test MVP cũ khi test cũ chưa truyền header auth.
    Mặc định True trên /api/queue và /api/block; có thể đặt RBAC_ENFORCE_LEGACY=false nếu cần tắt cho bộ test MVP cũ không truyền header auth.
    """
    return os.getenv("RBAC_ENFORCE_LEGACY", "true").strip().lower() in {"1", "true", "yes"}


def is_rbac_enforced_on_v1() -> bool:
    """Các endpoint mới /api/v1/workspace/* luôn bật RBAC 401/403 theo mặc định."""
    return os.getenv("RBAC_ENFORCE_V1", "true").strip().lower() in {"1", "true", "yes"}


def require_roles(allowed_roles: Iterable[str], enforce_flag_fn=is_rbac_enforced_on_v1):
    allowed_set = {r.upper() for r in allowed_roles}

    def _dependency(
        x_user_role: str | None = Header(default=None, alias="X-User-Role"),
        x_user_id: str | None = Header(default=None, alias="X-User-Id"),
        x_session_expired: str | None = Header(default=None, alias="X-Session-Expired"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AuthContext | None:
        enforce = enforce_flag_fn()
        # Nếu là legacy endpoint và không bật cờ ép buộc, nhưng caller vẫn chủ động gửi header auth/expired thì vẫn kiểm tra
        has_any_header = any([x_user_role, x_session_expired, authorization])
        if not enforce and not has_any_header:
            return None

        ctx = resolve_auth_context(
            x_user_role=x_user_role,
            x_user_id=x_user_id,
            x_session_expired=x_session_expired,
            authorization=authorization,
            require_auth=True,
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
    "ROLE_PERMISSIONS",
    "VALID_ROLES",
    "has_permission",
    "is_rbac_enforced_on_legacy",
    "is_rbac_enforced_on_v1",
    "require_roles",
    "resolve_auth_context",
]
