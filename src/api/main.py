"""PLPT-16, PLPT-17, PLPT-24 & PLPT-25: FastAPI Queue, Block, Workspace & RBAC API."""

from datetime import datetime
import contextlib
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.rules.rules_engine import RULES_LABEL, build_queue
from src.services.queue_service import (
    STALE_HOLD_HOURS,
    STALE_STATUS,
    VN_TZ,
    WorkspaceActionError,
    build_workspace_queue_payload,
    count_quarantine_rows,
    ensure_operational_tables,
    get_db_path,
    load_enquiries_and_meta,
    record_workspace_override,
    record_workspace_review,
)
import json
import pandas as pd
from src.models.serving_router import resolve_serving_route
from src.monitoring.drift_monitor import assess_mature_ranking_drift, compute_daily_monitoring_snapshot, persist_daily_drift_snapshot
from src.pilot.pilot_tracker import assign_or_get_pilot_treatment, log_pilot_effort_session, record_exposure_log, summarize_pilot_20bd
from src.models.shadow_runner import ensure_batch_run_table, ensure_shadow_score_table
from src.security.auth import (
    DEFAULT_SESSION_STORE,
    ensure_auth_tables,
    get_env_mode,
    get_session_secret_key,
    get_session_ttl_minutes,
    is_legacy_role_header_allowed,
    filter_records_by_auth_scope,
    get_internal_account,
    list_internal_accounts,
    record_security_audit_log,
    resolve_login_identity,
    upsert_internal_account,
)
from src.security.incident_hold import (
    activate_emergency_hold,
    get_active_incident_hold,
    record_purchaser_notification,
    release_emergency_hold,
)
from src.security.rbac import (
    VALID_ROLES,
    AuthContext,
    is_rbac_enforced_on_legacy,
    is_rbac_enforced_on_v1,
    require_roles,
)

app = FastAPI(
    title="SongNhat AI-02 Predictive Lead Prioritization API",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_denied_requests(request: Request, call_next):
    response = await call_next(request)
    if response.status_code in {401, 403}:
        record_security_audit_log(
            get_db_path(),
            event_type="AUTH_DENIED" if response.status_code == 401 else "AUTH_FORBIDDEN",
            endpoint=str(request.url.path),
            status_code=response.status_code,
            detail=f"{request.method} direct/API access denied",
        )
    return response


@app.middleware("http")
async def ensure_utf8_json_charset(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.lower() == "application/json":
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


def _get_db_path():
    return get_db_path()




def _assert_scope_for_action(
    *,
    enquiry_id: str = "",
    account_key: str = "",
    auth: AuthContext | None,
) -> None:
    """Quyết định PO #2 (SEC 01): Chặn IDOR khi SALES_READER/SUPERVISOR thao tác ngoài team_id/assigned_accounts."""
    if auth is None or auth.role not in {"SALES_READER", "SUPERVISOR"}:
        return
    if not auth.assigned_accounts and not auth.team_id:
        return

    target_acct = str(account_key or "").strip()
    target_team = ""
    enq = str(enquiry_id or "").strip()
    db_path = get_db_path()
    if enq and db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(clean_enquiries)").fetchall()}
            sel_cols = ["account_key"] + (["team_id"] if "team_id" in cols else [])
            row = conn.execute(
                f"SELECT {', '.join(sel_cols)} FROM clean_enquiries WHERE enquiry_id = ? LIMIT 1",
                (enq,),
            ).fetchone()
            if row is not None:
                if not target_acct:
                    target_acct = str(row[0] or "").strip()
                if len(row) > 1:
                    target_team = str(row[1] or "").strip()

    if auth.assigned_accounts:
        allowed_accts = {a.strip().upper() for a in auth.assigned_accounts if a.strip()}
        if not target_acct or target_acct.upper() not in allowed_accts:
            raise HTTPException(
                status_code=403,
                detail="Không có quyền thao tác trên hồ sơ thuộc account ngoài phạm vi được gán.",
            )
    if auth.team_id and target_team and target_team.upper() != auth.team_id.strip().upper():
        raise HTTPException(
            status_code=403,
            detail="Không có quyền thao tác trên hồ sơ thuộc team ngoài phạm vi được gán.",
        )

def _count_quarantine_rows() -> int:
    return count_quarantine_rows()


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_INDEX_PATH = FRONTEND_DIR / "index.html"


@app.get("/", include_in_schema=False)
def serve_workspace_index() -> FileResponse:
    if not FRONTEND_INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy giao diện frontend/index.html.")
    return FileResponse(FRONTEND_INDEX_PATH, media_type="text/html; charset=utf-8")


@app.get("/styles.css", include_in_schema=False)
def serve_workspace_css() -> FileResponse:
    css_path = FRONTEND_DIR / "styles.css"
    if not css_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy frontend/styles.css.")
    return FileResponse(css_path, media_type="text/css; charset=utf-8")


@app.get("/app.js", include_in_schema=False)
def serve_workspace_js() -> FileResponse:
    js_path = FRONTEND_DIR / "app.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy frontend/app.js.")
    return FileResponse(js_path, media_type="application/javascript; charset=utf-8")


@app.get("/pipeline.js", include_in_schema=False)
def serve_workspace_pipeline_js() -> FileResponse:
    js_path = FRONTEND_DIR / "pipeline.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy frontend/pipeline.js.")
    return FileResponse(js_path, media_type="application/javascript; charset=utf-8")


@app.get("/gate_g1.js", include_in_schema=False)
def serve_workspace_gate_g1_js() -> FileResponse:
    js_path = FRONTEND_DIR / "gate_g1.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy frontend/gate_g1.js.")
    return FileResponse(js_path, media_type="application/javascript; charset=utf-8")


@app.get("/sprint4.js", include_in_schema=False)
def serve_workspace_sprint4_js() -> FileResponse:
    js_path = FRONTEND_DIR / "sprint4.js"
    if not js_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy frontend/sprint4.js.")
    return FileResponse(js_path, media_type="application/javascript; charset=utf-8")


from src.api.gate_g1_routes import router as gate_g1_router
from src.api.pipeline_routes import router as pipeline_router
from src.api.sprint4_routes import router as sprint4_router

app.include_router(pipeline_router)
app.include_router(gate_g1_router)
app.include_router(sprint4_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/workspace/block")
def block_lead_or_account_v1(
    payload: dict[str, Any] = Body(...),
    _auth: AuthContext | None = Depends(
        require_roles({"SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    return block_lead_or_account(payload=payload, _auth=_auth)


@app.post("/api/block")
def block_lead_or_account(
    payload: dict[str, Any] = Body(...),
    _auth: AuthContext | None = Depends(
        require_roles({"SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_legacy)
    ),
) -> dict[str, Any]:
    """
    PLPT-17: Kích hoạt chặn khẩn cấp theo enquiry_id và/hoặc account_key.
    Hồ sơ bị chặn sẽ biến mất ngay lập tức khỏi GET /api/queue và /api/v1/workspace/queue.
    """
    enquiry_id = str(
        payload.get("enquiry_id")
        or payload.get("enquiry")
        or payload.get("lead_id")
        or payload.get("id")
        or ""
    ).strip()
    account_key = str(
        payload.get("account_key")
        or payload.get("account")
        or payload.get("customer_id")
        or ""
    ).strip()
    block_scope = str(payload.get("block_scope") or "").strip().lower()
    block_account = bool(payload.get("block_account")) or block_scope == "account"
    reason = str(
        payload.get("reason")
        or payload.get("block_reason")
        or "Emergency block by operator"
    ).strip()
    created_by = str(
        payload.get("created_by")
        or (_auth.user_id if _auth is not None else "PO_OPERATOR")
    ).strip()

    _assert_scope_for_action(enquiry_id=enquiry_id, account_key=account_key, auth=_auth)

    if not enquiry_id and not account_key:
        raise HTTPException(
            status_code=400,
            detail="Cần cung cấp ít nhất một trong hai trường: enquiry_id hoặc account_key.",
        )

    if enquiry_id and not block_account:
        target_enq: str | None = enquiry_id
        target_acct: str | None = None
    else:
        if not account_key:
            raise HTTPException(
                status_code=400,
                detail="Cần cung cấp account_key khi chọn chế độ chặn toàn tài khoản.",
            )
        target_enq = None
        target_acct = account_key

    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {db_path}")

    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    try:
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                ensure_operational_tables(conn)
                if target_enq is not None:
                    existing = conn.execute(
                        """
                        SELECT enquiry_id, account_key, reason, blocked_at
                        FROM blocked_accounts
                        WHERE enquiry_id = ?
                        LIMIT 1
                        """,
                        (target_enq,),
                    ).fetchone()
                else:
                    existing = conn.execute(
                        """
                        SELECT enquiry_id, account_key, reason, blocked_at
                        FROM blocked_accounts
                        WHERE COALESCE(enquiry_id, '') = '' AND account_key = ?
                        LIMIT 1
                        """,
                        (target_acct,),
                    ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO blocked_accounts (enquiry_id, account_key, reason, blocked_at, created_by)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (target_enq, target_acct, reason, now_iso, created_by),
                    )
                    conn.commit()
                    blocked_at = now_iso
                    enquiry_id = target_enq or ""
                    account_key = target_acct or ""
                else:
                    enquiry_id = existing[0] or ""
                    account_key = existing[1] or ""
                    reason = existing[2] or reason
                    blocked_at = existing[3] or now_iso

        return {
            "blocked": True,
            "status": "blocked",
            "enquiry_id": enquiry_id,
            "account_key": account_key,
            "reason": reason,
            "blocked_at": blocked_at,
        }
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.get("/api/queue")
def get_queue(
    limit: int = Query(default=25, ge=1, le=1000),
    _auth: AuthContext | None = Depends(
        require_roles(VALID_ROLES, enforce_flag_fn=is_rbac_enforced_on_legacy)
    ),
) -> dict[str, Any]:
    """
    Giữ nguyên schema GET /api/queue hiện tại để đảm bảo tương thích 100% với MVP & 35 unit test cũ,
    đồng thời dùng chung tầng service đọc DB, check HOLD_STALE_DATA (>24h) và lọc blocked_accounts.
    """
    try:
        (
            enquiries,
            blocked_enquiries,
            blocked_accounts,
            source_age_hours,
            _reviewed,
            _overridden,
            _steward_count,
        ) = load_enquiries_and_meta()
        now_vn = datetime.now(VN_TZ)
        scoped_enquiries = filter_records_by_auth_scope(
            enquiries,
            role=_auth.role if _auth else None,
            team_id=_auth.team_id if _auth else None,
            assigned_accounts=_auth.assigned_accounts if _auth else (),
        )
        queue = build_queue(scoped_enquiries, now=now_vn, exclude_closed=True)

        active_queue = []
        for entry in queue:
            enq_id = str(entry.get("enquiry_id") or "").strip()
            acct_key = str(entry.get("account_key") or "").strip()
            if (enq_id and enq_id in blocked_enquiries) or (
                acct_key and acct_key in blocked_accounts
            ):
                continue
            active_queue.append(entry)

        active_hold = get_active_incident_hold(get_db_path())
        is_stale = (active_hold is not None) or (source_age_hours is None) or (source_age_hours > STALE_HOLD_HOURS)
        status = STALE_STATUS if is_stale else "OK"
        if active_hold is not None:
            warning = f"Hàng đợi đang bị khóa khẩn cấp (SEC 08 incident_id={active_hold['incident_id']}): {active_hold['reason']}"
        elif source_age_hours is None:
            warning = (
                "Không xác minh được độ tươi dữ liệu nguồn (tập dữ liệu rỗng hoặc thiếu/sai định dạng extraction_time). "
                "Hàng đợi tạm khóa (HOLD_STALE_DATA) để đảm bảo an toàn vận hành."
            )
        elif is_stale:
            warning = (
                f"Dữ liệu nguồn đã cũ {source_age_hours} giờ (vượt ngưỡng 24 giờ). "
                "Hàng đợi tạm khóa (HOLD_STALE_DATA) để đảm bảo an toàn vận hành."
            )
        else:
            warning = None

        items: list[dict[str, Any]] = []
        if not is_stale:
            for idx, entry in enumerate(active_queue[:limit], start=1):
                items.append(
                    {
                        "enquiry_id": str(entry.get("enquiry_id") or ""),
                        "account_key": str(entry.get("account_key") or ""),
                        "final_rank": idx,
                        "rule_score": float(entry["rules_score"]),
                        "score_mode": RULES_LABEL,
                        "is_urgent": bool(entry["tier0_bypass"]),
                        "is_blocked": False,
                        "reasons": list(entry.get("reasons") or []),
                        "t0_time": str(entry.get("t0_time") or ""),
                    }
                )

        return {
            "status": status,
            "locked": is_stale,
            "warning": warning,
            "source_age_hours": source_age_hours,
            "quarantine_count": count_quarantine_rows(),
            "total": len(active_queue) if not is_stale else 0,
            "items": items,
            "data": items,
        }

    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.get("/api/v1/workspace/queue")
def get_workspace_queue(
    view: str = Query(default="active", pattern="^(active|unscored|quarantine)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=1000),
    q: str | None = Query(default=None),
    include_overflow: bool = Query(default=False),
    _auth: AuthContext | None = Depends(
        require_roles(VALID_ROLES, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-24: Endpoint Workspace mới hỗ trợ phân bổ K slot, overflow ngoài Top-K, 10% exploration, overdue 2BD & tìm kiếm tiếng Việt."""
    try:
        return build_workspace_queue_payload(
            view=view,
            page=page,
            limit=limit,
            query_text=q,
            include_overflow=include_overflow,
            auth_role=_auth.role if _auth else None,
            auth_team_id=_auth.team_id if _auth else None,
            auth_assigned_accounts=_auth.assigned_accounts if _auth else (),
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.post("/api/v1/workspace/review")
def post_workspace_review(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles({"SALES_READER", "SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-25: Ghi nhận ACCEPT_PRIORITY và xác minh quyền liên hệ khách hàng."""
    try:
        _assert_scope_for_action(
            enquiry_id=str(payload.get("enquiry_id") or ""),
            account_key=str(payload.get("account_key") or ""),
            auth=auth,
        )
        user_id = auth.user_id if auth is not None else "PO_OPERATOR"
        return record_workspace_review(payload, user_id=user_id)
    except WorkspaceActionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.post("/api/v1/workspace/override")
def post_workspace_override(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles({"SALES_READER", "SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-25 / CF-29: Ghi nhận OVERRIDE_PRIORITY vào staff_override_logs, cô lập hoàn toàn khỏi outcome_events."""
    try:
        _assert_scope_for_action(
            enquiry_id=str(payload.get("enquiry_id") or ""),
            account_key=str(payload.get("account_key") or ""),
            auth=auth,
        )
        user_id = auth.user_id if auth is not None else "PO_OPERATOR"
        return record_workspace_override(payload, user_id=user_id)
    except WorkspaceActionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")



# ============================================================================
# PLPT-60: Auth, Incident Hold (4h SLA) & Security Readiness Endpoints
# ============================================================================

@app.post("/auth/login")
@app.post("/api/v1/auth/login")
def auth_login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """PLPT-59 / PLPT-60 / Quyết định PO #1 & #2: Xác thực qua danh bạ nội bộ và cấp token HMAC-SHA256."""
    db_path = get_db_path()
    user_id = str(payload.get("user_id") or "").strip()
    role = str(payload.get("role") or "").strip().upper()
    role, team_id, assigned_accounts = resolve_login_identity(
        db_path,
        user_id=user_id,
        requested_role=role,
    )

    ttl_raw = payload.get("ttl_minutes")
    ttl_minutes: int | None = None
    if ttl_raw is not None:
        try:
            ttl_minutes = int(ttl_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="ttl_minutes phải là số nguyên dương.") from exc
        if get_env_mode() == "production":
            raise HTTPException(status_code=400, detail="Production không cho phép ghi đè TTL phiên.")

    record, token = DEFAULT_SESSION_STORE.issue_signed_session(
        user_id=user_id,
        role=role,
        ttl_minutes=ttl_minutes,
        team_id=team_id,
        assigned_accounts=assigned_accounts,
    )
    record_security_audit_log(
        get_db_path(),
        event_type="AUTH_LOGIN",
        user_id=record.user_id,
        role=record.role,
        endpoint="/auth/login",
        status_code=200,
        detail=f"session_id={record.session_id}",
    )
    return {
        "token": token,
        "token_type": "Bearer",
        "session_id": record.session_id,
        "user_id": record.user_id,
        "role": record.role,
        "issued_at": record.issued_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
    }


@app.post("/auth/logout")
@app.post("/api/v1/auth/logout")
def auth_logout(
    auth: AuthContext | None = Depends(
        require_roles(VALID_ROLES, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-59 / PLPT-60: Thu hồi phiên vào bảng revoked_sessions và ghi security_audit_logs."""
    db_path = get_db_path()
    sid = auth.session_id if auth and auth.session_id else ""
    uid = auth.user_id if auth else None
    role = auth.role if auth else None
    if sid:
        DEFAULT_SESSION_STORE.revoke_session(sid, db_path=db_path, user_id=uid, reason="USER_LOGOUT")
    record_security_audit_log(
        db_path,
        event_type="AUTH_LOGOUT",
        user_id=uid,
        role=role,
        endpoint="/auth/logout",
        status_code=200,
        detail=f"revoked session_id={sid}",
    )
    return {"revoked": True, "session_id": sid}


@app.get("/admin/incident-hold")
@app.get("/api/v1/admin/incident-hold")
def get_admin_incident_hold(
    _auth: AuthContext | None = Depends(
        require_roles({"SUPERVISOR", "ADMINISTRATOR", "MODEL_REVIEWER"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-60 / SEC-08: Kiểm tra trạng thái khóa sự cố và mẫu báo cáo 4 giờ cho Purchaser."""
    active = get_active_incident_hold(get_db_path())
    return {
        "active": active is not None,
        "incident": active,
        "sla_hours": 4.0,
        "notification_template": {
            "recipient": "SongNhat Purchaser Security & Business Owner",
            "required_within_hours": 4,
            "fields": ["incident_id", "severity", "affected_scope", "triggered_at", "containment_action"],
        },
    }


@app.post("/admin/incident-hold")
@app.post("/api/v1/admin/incident-hold")
def post_admin_incident_hold(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles({"ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-60 / SEC-08: Kích hoạt, ghi nhận thông báo 4h, hoặc mở khóa sự cố."""
    action = str(payload.get("action") or "activate").strip().lower()
    actor = auth.user_id if auth else "ADMINISTRATOR"
    db_path = get_db_path()

    try:
        if action == "activate":
            res = activate_emergency_hold(
                db_path,
                reason=str(payload.get("reason") or ""),
                triggered_by=actor,
                severity=str(payload.get("severity") or "CRITICAL"),
                affected_scope=str(payload.get("affected_scope") or "ALL_QUEUES"),
            )
        elif action == "notify":
            res = record_purchaser_notification(
                db_path,
                int(payload["incident_id"]),
                notified_by=actor,
                channel=str(payload.get("channel") or "OFFICIAL_EMAIL"),
            )
        elif action == "release":
            res = release_emergency_hold(
                db_path,
                int(payload["incident_id"]),
                released_by=actor,
                resolution_note=str(payload.get("resolution_note") or ""),
            )
        else:
            raise HTTPException(status_code=400, detail=f"action không hợp lệ: {action}")
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record_security_audit_log(
        db_path,
        event_type=f"INCIDENT_HOLD_{action.upper()}",
        user_id=actor,
        role=auth.role if auth else None,
        endpoint="/admin/incident-hold",
        status_code=200,
        detail=str(res),
    )
    return res


@app.get("/admin/security-readiness")
@app.get("/api/v1/admin/security-readiness")
def get_admin_security_readiness(
    auth: AuthContext | None = Depends(
        require_roles({"ADMINISTRATOR", "MODEL_REVIEWER", "SUPERVISOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-60 / SEC-06 / Gate G2: Kiểm tra cấu hình bảo mật, pháp lý Việt Nam, đào tạo Sales và nhật ký."""
    db_path = get_db_path()
    secret_ok = True
    try:
        secret = get_session_secret_key()
        secret_is_placeholder = secret == "CHANGE_ME_LOCAL_DEV_PLACEHOLDER_ONLY"
    except RuntimeError:
        secret_ok = False
        secret_is_placeholder = True

    revoked_count = 0
    audit_count = 0
    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                ensure_auth_tables(conn)
                revoked_count = int(conn.execute("SELECT COUNT(*) FROM revoked_sessions").fetchone()[0])
                audit_count = int(conn.execute("SELECT COUNT(*) FROM security_audit_logs").fetchone()[0])

    record_security_audit_log(
        db_path,
        event_type="SECURITY_READINESS_CHECK",
        user_id=auth.user_id if auth else None,
        role=auth.role if auth else None,
        endpoint="/api/v1/admin/security-readiness",
        status_code=200,
    )
    return {
        "env_mode": get_env_mode(),
        "rbac_enforced_v1": is_rbac_enforced_on_v1(),
        "rbac_enforced_legacy": is_rbac_enforced_on_legacy(),
        "allow_legacy_role_header": is_legacy_role_header_allowed(),
        "session_ttl_minutes": get_session_ttl_minutes(),
        "secret_configured": secret_ok and not secret_is_placeholder,
        "encryption_status": {
            "tls_in_transit": "UNKNOWN",
            "database_at_rest": "UNKNOWN",
            "backups_at_rest": "UNKNOWN",
            "exports_at_rest": "UNKNOWN",
        },
        "network_egress_status": "UNKNOWN",
        "active_sessions": DEFAULT_SESSION_STORE.active_session_count(),
        "revoked_sessions_count": revoked_count,
        "security_audit_logs_count": audit_count,
        "active_incident_hold": get_active_incident_hold(db_path),
        "vn_legal_compliance": {
            "reviewed_date": "2026-11-09",
            "dossier_ref": "docs/discovery/us_04_bao_mat_va_phap_ly_vietnam.md",
            "signoff_status": "PENDING_EXTERNAL_REAL_DATA_SIGNOFF",
            "instruments": [
                {
                    "code": "Luật 91/2025/QH15",
                    "name": "Luật Bảo vệ Dữ liệu Cá nhân",
                    "effective_date": "2026-01-01",
                    "status": "REVIEWED",
                },
                {
                    "code": "NĐ 356/2025/ND-CP",
                    "name": "Nghị định hướng dẫn Luật Bảo vệ Dữ liệu Cá nhân",
                    "effective_date": "2026-01-01",
                    "status": "REVIEWED",
                },
                {
                    "code": "Luật 134/2025/QH15",
                    "name": "Luật Trí tuệ nhân tạo",
                    "effective_date": "2026-03-01",
                    "status": "REVIEWED",
                },
                {
                    "code": "NĐ 142/2026/ND-CP",
                    "name": "Nghị định hướng dẫn Luật Trí tuệ nhân tạo",
                    "effective_date": "2026-05-01",
                    "status": "REVIEWED",
                },
                {
                    "code": "Quyết định 33/2026/QĐ-TTg",
                    "name": "Danh mục hệ thống AI có rủi ro cao",
                    "effective_date": "2026-07-02",
                    "status": "NON_HIGH_RISK_B2B_INTERNAL",
                },
                {
                    "code": "Luật 116/2025/QH15",
                    "name": "Luật An ninh mạng",
                    "effective_date": "2026-07-01",
                    "status": "REVIEWED",
                },
            ],
        },
        "sales_training_status": {
            "status": "PENDING_EXTERNAL_TRAINING_SIGNOFF",
            "completed_date": None,
            "target_users": 20,
            "max_concurrent_sessions": 5,
            "topics_covered": [
                "Ordinal rank vs conversion probability (non-causal)",
                "Contact permission verification before ACCEPT/OVERRIDE",
                "Emergency block & 2-business-day overdue handling",
            ],
        },
    }


# ============================================================================
# PLPT-65: Shadow Scoring Summary & Manual/Auto Rules Fallback Endpoints
# ============================================================================

@app.get("/shadow/summary")
@app.get("/api/v1/shadow/summary")
def get_shadow_summary(
    _auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR", "SUPERVISOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-65: Tổng hợp trạng thái định tuyến phục vụ, shadow_score_logs và batch_run_logs."""
    db_path = get_db_path()
    active_hold = get_active_incident_hold(db_path)
    gate_g1_path = Path(__file__).resolve().parents[2] / "artifacts" / "gate_g1" / "gate_g1_benchmark_report.json"
    manifest_path = Path(__file__).resolve().parents[2] / "artifacts" / "models" / "plpt-logreg-v1.0.0" / "manifest.json"
    decision = resolve_serving_route(
        gate_g1_report=gate_g1_path if gate_g1_path.exists() else None,
        manifest=manifest_path if manifest_path.exists() else None,
        incident_hold_active=active_hold is not None,
    )

    shadow_count = 0
    latest_batch = None
    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                ensure_shadow_score_table(conn)
                ensure_batch_run_table(conn)
                shadow_count = int(conn.execute("SELECT COUNT(*) FROM shadow_score_logs").fetchone()[0])
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM batch_run_logs ORDER BY id DESC LIMIT 1").fetchone()
                latest_batch = dict(row) if row is not None else None

    return {
        "serving_route": decision.to_dict(),
        "shadow_score_logs_count": shadow_count,
        "latest_batch_run": latest_batch,
    }


@app.post("/shadow/fallback")
@app.post("/api/v1/shadow/fallback")
def post_shadow_fallback(
    payload: dict[str, Any] = Body(default={}),
    auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-65: Ép hoặc kiểm tra cơ chế quay về RULES_BASELINE khi có sự cố/drift/chưa duyệt Gate G1."""
    drift_triggered = bool(payload.get("drift_fallback_triggered", True))
    control_ok = bool(payload.get("control_health_ok", True))
    decision = resolve_serving_route(
        requested_mode="PREDICTIVE_ML",
        drift_fallback_triggered=drift_triggered,
        control_health_ok=control_ok,
        incident_hold_active=get_active_incident_hold(get_db_path()) is not None,
    )
    record_security_audit_log(
        get_db_path(),
        event_type="SHADOW_FALLBACK_TRIGGERED",
        user_id=auth.user_id if auth else None,
        role=auth.role if auth else None,
        endpoint="/shadow/fallback",
        status_code=200,
        detail=str(decision.to_dict()),
    )
    return decision.to_dict()


@app.get("/admin/accounts")
@app.get("/api/v1/admin/accounts")
def get_admin_accounts(
    _auth: AuthContext | None = Depends(
        require_roles({"ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-59 / Quyết định PO #1: Danh bạ tài khoản nội bộ (tối đa 20 user)."""
    accounts = list_internal_accounts(get_db_path())
    return {"total": len(accounts), "max_users": 20, "accounts": accounts}


@app.post("/admin/accounts")
@app.post("/api/v1/admin/accounts")
def post_admin_accounts(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles({"ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-59 / Quyết định PO #1 & #2: Cấp/cập nhật/thu hồi tài khoản nội bộ gắn role, team_id, assigned_accounts."""
    db_path = get_db_path()
    res = upsert_internal_account(
        db_path,
        user_id=str(payload.get("user_id") or ""),
        role=str(payload.get("role") or ""),
        team_id=payload.get("team_id"),
        assigned_accounts=payload.get("assigned_accounts") or [],
        status=str(payload.get("status") or "ACTIVE"),
        created_by=auth.user_id if auth else "ADMINISTRATOR",
    )
    record_security_audit_log(
        db_path,
        event_type="ADMIN_ACCOUNT_UPSERT",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/admin/accounts",
        status_code=200,
        detail=f"target={res['user_id']} role={res['role']} status={res['status']}",
    )
    return res


# ============================================================================
# PLPT-68: Pilot Effort & Telemetry Endpoints (20 Business Days)
# ============================================================================

@app.post("/pilot/effort")
@app.post("/api/v1/pilot/effort")
def post_pilot_effort(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles({"SALES_READER", "SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-68: Ghi nhận công sức phân loại, triage_duration_seconds, phân bổ pilot_assignments và exposure_logs."""
    db_path = get_db_path()
    staff_id = str(payload.get("staff_id") or (auth.user_id if auth else "sales_staff")).strip()
    business_date = str(payload.get("business_date") or datetime.now(VN_TZ).date().isoformat()).strip()
    account_key = str(payload.get("account_key") or "").strip()
    project_ref_code = str(payload.get("project_ref_code") or "").strip()
    treatment_arm = str(payload.get("treatment_arm") or "").strip().upper()

    if (account_key or project_ref_code) and not treatment_arm:
        assign_res = assign_or_get_pilot_treatment(
            db_path,
            account_key=account_key or None,
            project_ref_code=project_ref_code or None,
        )
        treatment_arm = assign_res["treatment_arm"]
    elif not treatment_arm:
        treatment_arm = "RULES_CONTROL"

    try:
        res = log_pilot_effort_session(
            db_path,
            business_date=business_date,
            staff_id=staff_id,
            enquiries_reviewed=int(payload.get("enquiries_reviewed", 1)),
            queue_prep_minutes=float(payload.get("queue_prep_minutes", 0.0)),
            triage_minutes=float(payload["triage_minutes"]) if payload.get("triage_minutes") is not None else None,
            triage_duration_seconds=float(payload["triage_duration_seconds"]) if payload.get("triage_duration_seconds") is not None else None,
            due_total=int(payload.get("due_total", 0)),
            due_on_time=int(payload.get("due_on_time", 0)),
            override_count=int(payload.get("override_count", 0)),
            treatment_arm=treatment_arm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    enquiry_id = str(payload.get("enquiry_id") or "").strip()
    if enquiry_id:
        exp = record_exposure_log(
            db_path,
            enquiry_id=enquiry_id,
            account_key=account_key,
            staff_id=staff_id,
            treatment_arm=treatment_arm,
            scoring_mode=str(payload.get("scoring_mode") or RULES_LABEL),
            model_rank=payload.get("model_rank"),
        )
        res["exposure_log_id"] = exp["id"]

    res["treatment_arm"] = treatment_arm
    return res


@app.get("/pilot/telemetry")
@app.get("/api/v1/pilot/telemetry")
def get_pilot_telemetry(
    baseline_minutes_per_lead: float = Query(default=6.0, gt=0.0),
    baseline_on_time_rate: float | None = Query(default=None),
    _auth: AuthContext | None = Depends(
        require_roles({"SALES_READER", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-68: Tổng hợp telemetry pilot 20 ngày làm việc, pilot_assignments và exposure_logs."""
    return summarize_pilot_20bd(
        get_db_path(),
        baseline_minutes_per_lead=baseline_minutes_per_lead,
        baseline_on_time_rate=baseline_on_time_rate,
    )


# ============================================================================
# PLPT-72: Daily Drift, Missingness & Coverage Monitoring Endpoint (OPS-06)
# ============================================================================

@app.get("/api/v1/monitoring/drift")
def get_monitoring_drift(
    _auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR", "SUPERVISOR", "DATA_STEWARD"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-72 / OPS-06: Giám sát biến động khối lượng, tỷ lệ thiếu (>= 10pp), bao phủ (< 90%) và khóa cứng auto_retrain_enabled = false."""
    db_path = get_db_path()
    ref_path = Path(__file__).resolve().parents[2] / "artifacts" / "reference" / "drift_reference_baseline.json"
    ref_missing: dict[str, float] = {}
    if ref_path.exists():
        ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
        ref_missing = {str(k): float(v) for k, v in (ref_data.get("reference_missingness_pct") or {}).items()}

    enq_df = pd.DataFrame()
    out_df = pd.DataFrame()
    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            has_enq = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clean_enquiries'").fetchone()
            if has_enq:
                enq_df = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)
            has_out = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outcome_events'").fetchone()
            if has_out:
                out_df = pd.read_sql_query("SELECT * FROM outcome_events", conn)
            else:
                has_raw_out = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_outcomes'").fetchone()
                if has_raw_out:
                    out_df = pd.read_sql_query("SELECT * FROM raw_outcomes", conn)

    snap = compute_daily_monitoring_snapshot(
        enq_df,
        reference_missingness_pct=ref_missing,
    )
    if db_path.exists():
        snap["snapshot_id"] = persist_daily_drift_snapshot(str(db_path), snap)

    mature_status = assess_mature_ranking_drift(enq_df, out_df) if not enq_df.empty else {
        "mature_eligible_count": 0,
        "immature_excluded_count": 0,
        "known_mature_count": 0,
        "unknown_mature_count": 0,
        "mature_label_coverage_pct": 0.0,
        "ranking_drift_assessable": False,
        "ranking_drift_status": "DEFERRED_UNTIL_60D_MATURITY",
        "reason": "EMPTY_COHORT",
        "auto_retrain_allowed": False,
    }

    return {
        **snap,
        "ranking_drift_status": mature_status.get("ranking_drift_status", "DEFERRED_UNTIL_60D_MATURITY"),
        "mature_60d_status": mature_status,
        "auto_retrain_enabled": False,
        "auto_retrain_allowed": False,
    }
