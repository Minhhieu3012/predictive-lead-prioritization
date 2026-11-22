"""SEC-01 / PLPT-24 / PLPT-25: 5-Role Session Authentication & Concurrency Control."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets

from fastapi import HTTPException

VN_TZ = timezone(timedelta(hours=7))
MAX_CONCURRENT_SESSIONS = 5
DEFAULT_SESSION_TTL_MINUTES = 480

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
    session_id: str | None = None


@dataclass
class SessionRecord:
    session_id: str
    user_id: str
    role: str
    issued_at: datetime
    expires_at: datetime
    revoked: bool = False


class SessionStore:
    """
    Quản lý phiên đăng nhập nội bộ theo ranh giới thí điểm RFP (Part 2):
    - Hỗ trợ 5 vai trò RBAC (`VALID_ROLES`).
    - Giới hạn tối đa `max_concurrent_sessions` phiên hoạt động đồng thời (mặc định 5).
    - Từ chối phiên hết hạn (stale session) hoặc phiên đã thu hồi với HTTP 401.
    """

    def __init__(self, max_concurrent_sessions: int = MAX_CONCURRENT_SESSIONS) -> None:
        self.max_concurrent_sessions = max_concurrent_sessions
        self._sessions: dict[str, SessionRecord] = {}

    def _prune_expired(self, now: datetime) -> None:
        expired_ids = [
            sid
            for sid, rec in self._sessions.items()
            if rec.revoked or rec.expires_at <= now
        ]
        for sid in expired_ids:
            self._sessions.pop(sid, None)

    def create_session(
        self,
        user_id: str,
        role: str,
        *,
        ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES,
        now: datetime | None = None,
    ) -> SessionRecord:
        norm_role = str(role or "").strip().upper()
        if norm_role not in VALID_ROLES:
            raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {norm_role}")

        norm_user = str(user_id or "").strip()
        if not norm_user:
            raise HTTPException(status_code=400, detail="Thiếu mã người dùng (user_id).")

        current = now or datetime.now(VN_TZ)
        self._prune_expired(current)

        if len(self._sessions) >= self.max_concurrent_sessions:
            raise HTTPException(
                status_code=429,
                detail=f"Vượt quá giới hạn {self.max_concurrent_sessions} phiên đăng nhập đồng thời.",
            )

        sid = f"sn_{secrets.token_urlsafe(16)}"
        record = SessionRecord(
            session_id=sid,
            user_id=norm_user,
            role=norm_role,
            issued_at=current,
            expires_at=current + timedelta(minutes=ttl_minutes),
            revoked=False,
        )
        self._sessions[sid] = record
        return record

    def validate_session(self, session_id: str, *, now: datetime | None = None) -> AuthContext:
        current = now or datetime.now(VN_TZ)
        record = self._sessions.get(session_id)
        if record is None or record.revoked or record.expires_at <= current:
            raise HTTPException(
                status_code=401,
                detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn (stale/expired session).",
            )
        return AuthContext(user_id=record.user_id, role=record.role, session_id=record.session_id)

    def revoke_session(self, session_id: str) -> bool:
        record = self._sessions.get(session_id)
        if record is None:
            return False
        record.revoked = True
        self._sessions.pop(session_id, None)
        return True

    def active_session_count(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(VN_TZ)
        self._prune_expired(current)
        return len(self._sessions)


def resolve_auth_context(
    x_user_role: str | None = None,
    x_user_id: str | None = None,
    x_session_expired: str | None = None,
    authorization: str | None = None,
    require_auth: bool = True,
    session_store: SessionStore | None = None,
    now: datetime | None = None,
) -> AuthContext | None:
    if x_session_expired and x_session_expired.strip().lower() in {"1", "true", "yes"}:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn (stale/expired session).")

    role = (x_user_role or "").strip().upper()
    if not role and authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if session_store is not None and token.startswith("sn_"):
            return session_store.validate_session(token, now=now)
        if token.upper().startswith("ROLE:"):
            role = token[5:].strip().upper()
        elif token.upper() in VALID_ROLES:
            role = token.upper()
        elif token.lower() in {"expired", "invalid", "stale"}:
            raise HTTPException(status_code=401, detail="Token phiên không hợp lệ hoặc đã hết hạn.")

    if not role:
        if require_auth:
            raise HTTPException(
                status_code=401,
                detail="Thiếu thông tin xác thực vai trò (X-User-Role hoặc Authorization).",
            )
        return None

    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {role}")

    user_id = (x_user_id or f"user_{role.lower()}").strip()
    return AuthContext(user_id=user_id, role=role)
