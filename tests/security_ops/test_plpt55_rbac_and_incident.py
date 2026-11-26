"""Tests cho PLPT-55 / SEC-01 / SEC-08: Ma trận RBAC 5 vai trò, giới hạn 5 phiên & khóa sự cố 4 giờ."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
import pytest

from src.security.auth import (
    DEFAULT_PLACEHOLDER_SECRET,
    MAX_CONCURRENT_SESSIONS,
    SessionStore,
    get_env_mode,
    get_session_secret_key,
    get_session_ttl_minutes,
    resolve_auth_context,
)
from src.security.incident_hold import (
    HOLD_STALE_DATA,
    activate_emergency_hold,
    commit_local_emergency_block,
    get_active_incident_hold,
    record_purchaser_notification,
    release_emergency_hold,
)
from src.security.rbac import ROLE_PERMISSIONS, VALID_ROLES, has_permission

VN_TZ = timezone(timedelta(hours=7))


def test_plpt55_five_role_rbac_matrix_and_session_limits():
    assert VALID_ROLES == {
        "SALES_READER",
        "SUPERVISOR",
        "DATA_STEWARD",
        "MODEL_REVIEWER",
        "ADMINISTRATOR",
    }
    assert set(ROLE_PERMISSIONS.keys()) == VALID_ROLES

    # Kiểm tra ranh giới quyền hạn chính giữa 5 vai trò
    assert has_permission("SALES_READER", "workspace:override") is True
    assert has_permission("SALES_READER", "workspace:block") is False
    assert has_permission("SUPERVISOR", "workspace:block") is True
    assert has_permission("DATA_STEWARD", "quarantine:resolve") is True
    assert has_permission("DATA_STEWARD", "workspace:override") is False
    assert has_permission("MODEL_REVIEWER", "model:fallback") is True
    assert has_permission("ADMINISTRATOR", "ops:backup") is True

    # Giới hạn tối đa 5 phiên đăng nhập đồng thời (RFP Part 2)
    store = SessionStore(max_concurrent_sessions=MAX_CONCURRENT_SESSIONS)
    base_now = datetime(2026, 11, 22, 9, 0, tzinfo=VN_TZ)
    sessions = [store.create_session(f"sales_{i}", "SALES_READER", now=base_now) for i in range(5)]
    assert store.active_session_count(now=base_now) == 5

    with pytest.raises(HTTPException) as exc_429:
        store.create_session("sales_6", "SALES_READER", now=base_now)
    assert exc_429.value.status_code == 429

    # Phiên hợp lệ -> OK; quá hạn TTL -> 401; thu hồi -> 401
    ctx = resolve_auth_context(
        authorization=f"Bearer {sessions[0].session_id}",
        session_store=store,
        now=base_now + timedelta(hours=1),
    )
    assert ctx is not None and ctx.user_id == "sales_0" and ctx.role == "SALES_READER"

    with pytest.raises(HTTPException) as exc_expired:
        resolve_auth_context(
            authorization=f"Bearer {sessions[0].session_id}",
            session_store=store,
            now=base_now + timedelta(hours=9),
        )
    assert exc_expired.value.status_code == 401

    assert store.revoke_session(sessions[0].session_id) is True
    with pytest.raises(HTTPException) as exc_revoked:
        store.validate_session(sessions[0].session_id, now=base_now)
    assert exc_revoked.value.status_code == 401


def test_sec08_four_hour_incident_hold_and_local_block(tmp_path: Path):
    db_path = tmp_path / "incident.db"
    t0 = datetime(2026, 11, 22, 10, 0, tzinfo=VN_TZ)

    hold = activate_emergency_hold(
        db_path,
        reason="Nghi ngo ro ri PII trong file CSV",
        triggered_by="admin_01",
        severity="CRITICAL",
        now=t0,
    )
    assert hold["hold_code"] == HOLD_STALE_DATA
    assert get_active_incident_hold(db_path) is not None

    notif = record_purchaser_notification(
        db_path,
        hold["incident_id"],
        notified_by="admin_01",
        now=t0 + timedelta(hours=3, minutes=30),
    )
    assert notif["within_4h_sla"] is True

    released = release_emergency_hold(
        db_path,
        hold["incident_id"],
        released_by="admin_01",
        resolution_note="Da va loi va chay lai 40 control fixtures",
        now=t0 + timedelta(hours=5),
    )
    assert released["status"] == "RESOLVED"
    assert get_active_incident_hold(db_path) is None

    blk = commit_local_emergency_block(
        db_path,
        enquiry_id="SN-BLK-1",
        reason="Khach yeu cau ngung lien he",
        created_by="sup_01",
    )
    assert blk["blocked"] is True


def test_plpt55_env_separation_and_secret_management(monkeypatch):
    monkeypatch.setenv("ENV_MODE", "evaluation")
    monkeypatch.setenv("SESSION_TTL_MINUTES", "120")
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    assert get_env_mode() == "evaluation"
    assert get_session_ttl_minutes() == 120
    assert get_session_secret_key() == DEFAULT_PLACEHOLDER_SECRET

    # Ở production, bắt buộc có SESSION_SECRET_KEY thật >= 32 ký tự và không được dùng placeholder
    monkeypatch.setenv("ENV_MODE", "production")
    monkeypatch.setenv("SESSION_SECRET_KEY", DEFAULT_PLACEHOLDER_SECRET)
    with pytest.raises(RuntimeError):
        get_session_secret_key()

    monkeypatch.setenv("SESSION_SECRET_KEY", "x" * 40)
    assert len(get_session_secret_key()) == 40

