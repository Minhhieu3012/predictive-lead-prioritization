"""PLPT-24 / PLPT-25 / SEC-01: 5-Role RBAC & Session Validation."""

from dataclasses import dataclass
import os
from typing import Iterable

from fastapi import Header, HTTPException

VALID_ROLES = {
    "SALES_READER",
    "SUPERVISOR",
    "DATA_STEWARD",
    "MODEL_REVIEWER",
    "ADMINISTRATOR",
}


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    role: str


def is_rbac_enforced_on_legacy() -> bool:
    """
    Giữ cờ cấu hình để không làm gãy 35 unit test MVP cũ khi test cũ chưa truyền header auth.
    Mặc định False trên /api/queue và /api/block nếu không có header; bật True qua RBAC_ENFORCE_LEGACY=true.
    """
    return os.getenv("RBAC_ENFORCE_LEGACY", "true").strip().lower() in {"1", "true", "yes"}


def is_rbac_enforced_on_v1() -> bool:
    """Các endpoint mới /api/v1/workspace/* luôn bật RBAC 401/403 theo mặc định."""
    return os.getenv("RBAC_ENFORCE_V1", "true").strip().lower() in {"1", "true", "yes"}


def resolve_auth_context(
    x_user_role: str | None = None,
    x_user_id: str | None = None,
    x_session_expired: str | None = None,
    authorization: str | None = None,
    require_auth: bool = True,
) -> AuthContext | None:
    if x_session_expired and x_session_expired.strip().lower() in {"1", "true", "yes"}:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn (stale/expired session).")

    role = (x_user_role or "").strip().upper()
    if not role and authorization:
        # Hỗ trợ dạng "Bearer ROLE:SALES_READER" hoặc "Bearer SALES_READER"
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token.upper().startswith("ROLE:"):
            role = token[5:].strip().upper()
        elif token.upper() in VALID_ROLES:
            role = token.upper()
        elif token.lower() in {"expired", "invalid", "stale"}:
            raise HTTPException(status_code=401, detail="Token phiên không hợp lệ hoặc đã hết hạn.")

    if not role:
        if require_auth:
            raise HTTPException(status_code=401, detail="Thiếu thông tin xác thực vai trò (X-User-Role hoặc Authorization).")
        return None

    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {role}")

    user_id = (x_user_id or f"user_{role.lower()}").strip()
    return AuthContext(user_id=user_id, role=role)


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
