"""SEC-01 / PLPT-59 / PLPT-60: 5-Role HMAC-SHA256 Session Authentication, Revocation & Concurrency Control."""

import base64
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from fastapi import HTTPException

VN_TZ = timezone(timedelta(hours=7))
VALID_ENV_MODES = {"development", "evaluation", "production"}
DEFAULT_PLACEHOLDER_SECRET = "CHANGE_ME_LOCAL_DEV_PLACEHOLDER_ONLY"
MAX_CONCURRENT_SESSIONS = 5
MAX_INTERNAL_USERS = 20
GLOBAL_SCOPE_ROLES = {"ADMINISTRATOR", "DATA_STEWARD", "MODEL_REVIEWER"}
DEFAULT_SESSION_TTL_MINUTES = 60
MAX_SESSION_TTL_MINUTES = 480

VALID_ROLES = {
    "SALES_READER",
    "SUPERVISOR",
    "DATA_STEWARD",
    "MODEL_REVIEWER",
    "ADMINISTRATOR",
}


def get_env_mode() -> str:
    """SEC-06 / PLPT-55: Xác định môi trường vận hành (development | evaluation | production)."""
    mode = os.getenv("ENV_MODE", "development").strip().lower()
    if mode not in VALID_ENV_MODES:
        raise ValueError(f"ENV_MODE không hợp lệ: {mode}. Chỉ chấp nhận {sorted(VALID_ENV_MODES)}.")
    return mode


def get_session_ttl_minutes() -> int:
    raw = os.getenv("SESSION_TTL_MINUTES", str(DEFAULT_SESSION_TTL_MINUTES)).strip()
    try:
        val = int(raw)
        if val <= 0:
            return DEFAULT_SESSION_TTL_MINUTES
        if val > MAX_SESSION_TTL_MINUTES:
            return DEFAULT_SESSION_TTL_MINUTES
        return val
    except ValueError:
        return DEFAULT_SESSION_TTL_MINUTES


def get_session_secret_key() -> str:
    """
    SEC-06 / PLPT-55: Đọc khóa bí mật phiên từ biến môi trường (tách khỏi tệp dữ liệu).
    Ở ENV_MODE=production bắt buộc cung cấp khóa thật >= 32 ký tự và khác placeholder.
    """
    mode = get_env_mode()
    secret = os.getenv("SESSION_SECRET_KEY", "").strip()
    if mode == "production":
        if not secret or secret == DEFAULT_PLACEHOLDER_SECRET or len(secret) < 32:
            raise RuntimeError(
                "ENV_MODE=production yêu cầu SESSION_SECRET_KEY thật (>= 32 ký tự, không dùng placeholder)."
            )
    return secret or DEFAULT_PLACEHOLDER_SECRET


def is_legacy_role_header_allowed() -> bool:
    """PLPT-59: Mặc định chặn header X-User-Role thô để chống giả mạo vai trò qua Direct URL."""
    return os.getenv("ALLOW_LEGACY_ROLE_HEADER", "false").strip().lower() in {"1", "true", "yes"}


def ensure_auth_tables(conn: sqlite3.Connection) -> None:
    """PLPT-59 / PLPT-60: Khởi tạo bảng revoked_sessions và security_audit_logs."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS revoked_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            revoked_at TEXT NOT NULL,
            reason TEXT DEFAULT 'LOGOUT'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS internal_accounts (
            user_id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            team_id TEXT,
            assigned_accounts_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_by TEXT DEFAULT 'ADMINISTRATOR',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            user_id TEXT,
            role TEXT,
            endpoint TEXT,
            status_code INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL
        )
        """
    )


def record_security_audit_log(
    db_path: str | Path,
    *,
    event_type: str,
    user_id: str | None = None,
    role: str | None = None,
    endpoint: str | None = None,
    status_code: int | None = None,
    detail: str | None = None,
    now: datetime | None = None,
) -> None:
    p = Path(db_path)
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        with conn:
            ensure_auth_tables(conn)
            conn.execute(
                """
                INSERT INTO security_audit_logs (
                    event_type, user_id, role, endpoint, status_code, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_type, user_id, role, endpoint, status_code, detail, ts),
            )
            conn.commit()


def is_session_revoked(db_path: str | Path | None, session_id: str) -> bool:
    if db_path is None:
        return False
    p = Path(db_path)
    if not p.exists():
        return False
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        has_tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='revoked_sessions'"
        ).fetchone()
        if not has_tbl:
            return False
        row = conn.execute(
            "SELECT 1 FROM revoked_sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        return row is not None


def mark_session_revoked(
    db_path: str | Path,
    session_id: str,
    *,
    user_id: str | None = None,
    reason: str = "LOGOUT",
    now: datetime | None = None,
) -> None:
    ts = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_auth_tables(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO revoked_sessions (session_id, user_id, revoked_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, user_id, ts, reason),
            )
            conn.commit()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_session_token(
    session_id: str,
    user_id: str,
    role: str,
    issued_at: datetime,
    expires_at: datetime,
    *,
    team_id: str | None = None,
    assigned_accounts: tuple[str, ...] | list[str] = (),
) -> str:
    """PLPT-59: Tạo token phiên ký HMAC-SHA256 (định dạng sn1.<payload_b64>.<sig_hex>)."""
    payload = {
        "sid": session_id,
        "uid": user_id,
        "role": role,
        "team_id": team_id,
        "accounts": [str(a).strip() for a in assigned_accounts if str(a).strip()],
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    secret = get_session_secret_key().encode("utf-8")
    sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"sn1.{payload_b64}.{sig}"


def verify_signed_session_token(
    token: str,
    *,
    db_path: str | Path | None = None,
    now: datetime | None = None,
) -> "AuthContext":
    """PLPT-59: Xác thực chữ ký HMAC-SHA256, TTL và bảng revoked_sessions."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "sn1":
        raise HTTPException(status_code=401, detail="Định dạng token phiên không hợp lệ.")
    _, payload_b64, sig = parts
    secret = get_session_secret_key().encode("utf-8")
    expected_sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(status_code=401, detail="Chữ ký token phiên HMAC-SHA256 không hợp lệ.")

    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Payload token phiên bị lỗi.") from exc

    sid = str(payload.get("sid") or "").strip()
    uid = str(payload.get("uid") or "").strip()
    role = str(payload.get("role") or "").strip().upper()
    exp_ts = payload.get("exp")

    if not sid or not uid or exp_ts is None:
        raise HTTPException(status_code=401, detail="Token phiên thiếu trường bắt buộc.")

    current_ts = int((now or datetime.now(VN_TZ)).timestamp())
    if int(exp_ts) <= current_ts:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn (stale/expired session).")

    if is_session_revoked(db_path, sid):
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã bị thu hồi (revoked session).")

    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {role}")

    team_id = str(payload.get("team_id") or "").strip() or None
    accts = tuple(str(a).strip() for a in (payload.get("accounts") or []) if str(a).strip())
    if db_path is not None:
        account = get_internal_account(db_path, uid)
        if account is not None:
            if account["status"] != "ACTIVE":
                raise HTTPException(status_code=403, detail="Tài khoản nội bộ đã bị thu hồi hoặc khóa.")
            if str(account["role"]).strip().upper() != role:
                raise HTTPException(status_code=403, detail="Vai trò phiên không khớp danh bạ nội bộ.")
            team_id = account["team_id"]
            accts = tuple(str(a).strip() for a in account["assigned_accounts"] if str(a).strip())
    return AuthContext(user_id=uid, role=role, session_id=sid, team_id=team_id, assigned_accounts=accts)


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    role: str
    session_id: str | None = None
    team_id: str | None = None
    assigned_accounts: tuple[str, ...] = ()


@dataclass
class SessionRecord:
    session_id: str
    user_id: str
    role: str
    issued_at: datetime
    expires_at: datetime
    revoked: bool = False
    team_id: str | None = None
    assigned_accounts: tuple[str, ...] = ()


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
        ttl_minutes: int | None = None,
        team_id: str | None = None,
        assigned_accounts: tuple[str, ...] | list[str] = (),
        now: datetime | None = None,
    ) -> SessionRecord:
        effective_ttl = ttl_minutes if ttl_minutes is not None else get_session_ttl_minutes()
        if effective_ttl <= 0:
            raise HTTPException(status_code=400, detail="SESSION_TTL_MINUTES phải là số nguyên dương.")
        if effective_ttl > MAX_SESSION_TTL_MINUTES:
            raise HTTPException(
                status_code=400,
                detail=f"TTL phiên không được vượt quá {MAX_SESSION_TTL_MINUTES} phút.",
            )
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
        norm_accts = tuple(str(a).strip() for a in assigned_accounts if str(a).strip())
        norm_team = str(team_id).strip() if team_id else None
        record = SessionRecord(
            session_id=sid,
            user_id=norm_user,
            role=norm_role,
            issued_at=current,
            expires_at=current + timedelta(minutes=effective_ttl),
            revoked=False,
            team_id=norm_team,
            assigned_accounts=norm_accts,
        )
        self._sessions[sid] = record
        return record

    def issue_signed_session(
        self,
        user_id: str,
        role: str,
        *,
        ttl_minutes: int | None = None,
        team_id: str | None = None,
        assigned_accounts: tuple[str, ...] | list[str] = (),
        now: datetime | None = None,
    ) -> tuple[SessionRecord, str]:
        record = self.create_session(
            user_id,
            role,
            ttl_minutes=ttl_minutes,
            team_id=team_id,
            assigned_accounts=assigned_accounts,
            now=now,
        )
        token = sign_session_token(
            record.session_id,
            record.user_id,
            record.role,
            record.issued_at,
            record.expires_at,
            team_id=record.team_id,
            assigned_accounts=record.assigned_accounts,
        )
        return record, token

    def validate_session(
        self,
        session_id: str,
        *,
        db_path: str | Path | None = None,
        now: datetime | None = None,
    ) -> AuthContext:
        if is_session_revoked(db_path, session_id):
            raise HTTPException(
                status_code=401,
                detail="Phiên đăng nhập đã bị thu hồi (revoked session).",
            )
        current = now or datetime.now(VN_TZ)
        record = self._sessions.get(session_id)
        if record is None or record.revoked or record.expires_at <= current:
            raise HTTPException(
                status_code=401,
                detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn (stale/expired session).",
            )
        return AuthContext(
            user_id=record.user_id,
            role=record.role,
            session_id=record.session_id,
            team_id=record.team_id,
            assigned_accounts=record.assigned_accounts,
        )

    def revoke_session(
        self,
        session_id: str,
        *,
        db_path: str | Path | None = None,
        user_id: str | None = None,
        reason: str = "LOGOUT",
    ) -> bool:
        record = self._sessions.get(session_id)
        if db_path is not None:
            mark_session_revoked(
                db_path,
                session_id,
                user_id=user_id or (record.user_id if record else None),
                reason=reason,
            )
        if record is None:
            return db_path is not None
        record.revoked = True
        self._sessions.pop(session_id, None)
        return True

    def revoke_sessions_for_user(
        self,
        user_id: str,
        *,
        db_path: str | Path | None = None,
        reason: str = "ACCOUNT_REVOKED",
    ) -> int:
        norm_user = str(user_id or "").strip()
        revoked_count = 0
        for session_id, record in list(self._sessions.items()):
            if record.user_id == norm_user:
                self.revoke_session(
                    session_id,
                    db_path=db_path,
                    user_id=norm_user,
                    reason=reason,
                )
                revoked_count += 1
        return revoked_count

    def active_session_count(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(VN_TZ)
        self._prune_expired(current)
        return len(self._sessions)


DEFAULT_SESSION_STORE = SessionStore(max_concurrent_sessions=MAX_CONCURRENT_SESSIONS)


def resolve_auth_context(
    x_user_role: str | None = None,
    x_user_id: str | None = None,
    x_session_expired: str | None = None,
    authorization: str | None = None,
    require_auth: bool = True,
    session_store: SessionStore | None = None,
    db_path: str | Path | None = None,
    now: datetime | None = None,
) -> AuthContext | None:
    if x_session_expired and x_session_expired.strip().lower() in {"1", "true", "yes"}:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn (stale/expired session).")

    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token.startswith("sn1."):
            return verify_signed_session_token(token, db_path=db_path, now=now)
        if session_store is not None and token.startswith("sn_"):
            return session_store.validate_session(token, db_path=db_path, now=now)
        if token.lower() in {"expired", "invalid", "stale"}:
            raise HTTPException(status_code=401, detail="Token phiên không hợp lệ hoặc đã hết hạn.")
        if is_legacy_role_header_allowed():
            if get_env_mode() == "production":
                raise HTTPException(
                    status_code=401,
                    detail="Môi trường production yêu cầu token phiên HMAC-SHA256.",
                )
            legacy_role = ""
            if token.upper().startswith("ROLE:"):
                legacy_role = token[5:].strip().upper()
            elif token.upper() in VALID_ROLES:
                legacy_role = token.upper()
            if legacy_role:
                if legacy_role not in VALID_ROLES:
                    raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {legacy_role}")
                return AuthContext(user_id=(x_user_id or f"user_{legacy_role.lower()}").strip(), role=legacy_role)
        raise HTTPException(status_code=401, detail="Token phiên không hợp lệ hoặc chưa ký HMAC-SHA256.")

    role = (x_user_role or "").strip().upper()
    if role:
        if get_env_mode() == "production":
            raise HTTPException(
                status_code=401,
                detail="Môi trường production không chấp nhận header vai trò thô.",
            )
        if not is_legacy_role_header_allowed():
            raise HTTPException(
                status_code=401,
                detail="Chặn Direct URL: không chấp nhận header X-User-Role thô, yêu cầu Authorization Bearer HMAC-SHA256.",
            )
        if role not in VALID_ROLES:
            raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {role}")
        user_id = (x_user_id or f"user_{role.lower()}").strip()
        return AuthContext(user_id=user_id, role=role)

    if require_auth:
        raise HTTPException(
            status_code=401,
            detail="Thiếu thông tin xác thực phiên (Authorization: Bearer <token>).",
        )
    return None


def resolve_login_identity(
    db_path: str | Path,
    *,
    user_id: str,
    requested_role: str = "",
) -> tuple[str, str | None, tuple[str, ...]]:
    """
    Resolve the authentication contract for a login request.

    Internal accounts are authoritative: if present, role, team, and assigned
    accounts come from the account directory and caller-supplied scope is ignored.
    Unknown accounts are allowed only in development/evaluation fallback mode,
    require an explicit approved role, and receive no team/account scope.
    """
    norm_user = str(user_id or "").strip()
    if not norm_user:
        raise HTTPException(status_code=400, detail="Thiếu mã người dùng (user_id).")

    norm_role = str(requested_role or "").strip().upper()
    account = get_internal_account(db_path, norm_user)

    if account is not None:
        if account["status"] != "ACTIVE":
            raise HTTPException(status_code=403, detail="Tài khoản nội bộ đã bị thu hồi hoặc khóa.")
        directory_role = str(account["role"]).strip().upper()
        if norm_role and norm_role != directory_role:
            raise HTTPException(status_code=403, detail="Vai trò yêu cầu không khớp danh bạ nội bộ.")
        team_id = account["team_id"]
        assigned_accounts = tuple(str(account_key).strip() for account_key in account["assigned_accounts"] if str(account_key).strip())
        return directory_role, team_id, assigned_accounts

    require_directory = (
        get_env_mode() == "production"
        or os.getenv("REQUIRE_ACCOUNT_DIRECTORY", "false").strip().lower() in {"1", "true", "yes"}
    )
    if require_directory:
        raise HTTPException(status_code=401, detail="Tài khoản chưa được ADMINISTRATOR cấp trong danh bạ nội bộ.")

    if not norm_role:
        raise HTTPException(status_code=400, detail="Thiếu vai trò đã được chấp thuận (role).")
    if norm_role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {norm_role}")

    return norm_role, None, ()


def upsert_internal_account(
    db_path: str | Path,
    *,
    user_id: str,
    role: str,
    team_id: str | None = None,
    assigned_accounts: list[str] | tuple[str, ...] = (),
    status: str = "ACTIVE",
    created_by: str = "ADMINISTRATOR",
    now: datetime | None = None,
) -> dict[str, Any]:
    """PLPT-59 / Quyết định PO #1 & #2: Quản lý danh bạ 20 tài khoản nội bộ theo role, team_id & assigned_accounts."""
    norm_user = str(user_id or "").strip()
    norm_role = str(role or "").strip().upper()
    norm_status = str(status or "ACTIVE").strip().upper()
    if not norm_user:
        raise HTTPException(status_code=400, detail="Thiếu user_id.")
    if norm_role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"Vai trò không hợp lệ: {norm_role}")
    if norm_status not in {"ACTIVE", "REVOKED"}:
        raise HTTPException(status_code=400, detail=f"Trạng thái tài khoản không hợp lệ: {norm_status}")

    norm_team = str(team_id).strip() if team_id else None
    norm_accts = [str(a).strip() for a in assigned_accounts if str(a).strip()]
    ts = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()

    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        with conn:
            ensure_auth_tables(conn)
            existing = conn.execute(
                "SELECT user_id, created_at FROM internal_accounts WHERE user_id = ?",
                (norm_user,),
            ).fetchone()
            if existing is None and norm_status == "ACTIVE":
                active_cnt = int(
                    conn.execute("SELECT COUNT(*) FROM internal_accounts WHERE status = 'ACTIVE'").fetchone()[0]
                )
                if active_cnt >= MAX_INTERNAL_USERS:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Vượt quá giới hạn {MAX_INTERNAL_USERS} tài khoản nội bộ ACTIVE (RFP Part 2).",
                    )
            created_at = existing[1] if existing is not None else ts
            conn.execute(
                """
                INSERT OR REPLACE INTO internal_accounts (
                    user_id, role, team_id, assigned_accounts_json, status, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (norm_user, norm_role, norm_team, json.dumps(norm_accts, ensure_ascii=False), norm_status, created_by, created_at, ts),
            )
            conn.commit()
    if norm_status == "REVOKED":
        DEFAULT_SESSION_STORE.revoke_sessions_for_user(
            norm_user,
            db_path=p,
            reason="ACCOUNT_REVOKED",
        )
    return {
        "user_id": norm_user,
        "role": norm_role,
        "team_id": norm_team,
        "assigned_accounts": norm_accts,
        "status": norm_status,
        "updated_at": ts,
    }


def get_internal_account(db_path: str | Path, user_id: str) -> dict[str, Any] | None:
    p = Path(db_path)
    if not p.exists():
        return None
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        has_tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='internal_accounts'"
        ).fetchone()
        if not has_tbl:
            return None
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM internal_accounts WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "role": row["role"],
            "team_id": row["team_id"],
            "assigned_accounts": json.loads(row["assigned_accounts_json"] or "[]"),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def list_internal_accounts(db_path: str | Path) -> list[dict[str, Any]]:
    p = Path(db_path)
    if not p.exists():
        return []
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        has_tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='internal_accounts'"
        ).fetchone()
        if not has_tbl:
            return []
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM internal_accounts ORDER BY user_id ASC").fetchall()
        return [
            {
                "user_id": r["user_id"],
                "role": r["role"],
                "team_id": r["team_id"],
                "assigned_accounts": json.loads(r["assigned_accounts_json"] or "[]"),
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


def filter_records_by_auth_scope(
    records: list[dict[str, Any]],
    *,
    role: str | None = None,
    team_id: str | None = None,
    assigned_accounts: tuple[str, ...] | list[str] = (),
) -> list[dict[str, Any]]:
    """
    Quyết định PO #2 (SEC 01):
    - ADMINISTRATOR, DATA_STEWARD, MODEL_REVIEWER giữ phạm vi toàn cục.
    - SALES_READER và SUPERVISOR lọc theo team_id và/hoặc assigned_accounts được gán.
    """
    norm_role = str(role or "").strip().upper()
    if not norm_role or norm_role in GLOBAL_SCOPE_ROLES:
        return records
    allowed_accts = {str(a).strip().upper() for a in assigned_accounts if str(a).strip()}
    norm_team = str(team_id or "").strip().upper()
    if not allowed_accts and not norm_team:
        return records

    filtered: list[dict[str, Any]] = []
    for row in records:
        if allowed_accts:
            acct = str(row.get("account_key") or "").strip().upper()
            if acct not in allowed_accts:
                continue
        if norm_team:
            row_team = str(row.get("team_id") or row.get("assigned_team") or "").strip().upper()
            if row_team and row_team != norm_team:
                continue
        filtered.append(row)
    return filtered
