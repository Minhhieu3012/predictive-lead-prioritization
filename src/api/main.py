"""PLPT-16, PLPT-17, PLPT-24 & PLPT-25: FastAPI Queue, Block, Workspace & RBAC API."""

from datetime import datetime
import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.rules.rules_engine import RULES_LABEL, build_queue
from src.models.serving_router import (
    DEFAULT_SERVING_MODE,
    FALLBACK_SERVING_MODE,
    PREDICTIVE_SERVING_MODE,
    ServingRouteDecision,
    get_manual_fallback_state,
    get_serving_health,
    reset_manual_fallback,
    resolve_serving_route,
    trigger_manual_fallback,
)
from src.models.shadow_runner import get_shadow_summary
from src.models.train_candidates import get_registered_model, list_registered_models
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
from src.security.rbac import (
    VALID_ROLES,
    AuthContext,
    is_rbac_enforced_on_legacy,
    is_rbac_enforced_on_v1,
    require_roles,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def _get_db_path():
    return get_db_path()


def _count_quarantine_rows() -> int:
    return count_quarantine_rows()


def _resolve_shadow_route(
    db_path: Path,
    *,
    requested_mode: str | None = None,
) -> tuple[
    ServingRouteDecision,
    dict[str, Any] | None,
    dict[str, Any] | None,
    str | None,
]:
    health = get_serving_health(db_path)
    route = resolve_serving_route(
        requested_mode=(
            requested_mode
            or os.getenv("ACTIVE_SCORING_MODE", DEFAULT_SERVING_MODE).strip().upper()
        ),
        gate_g1_report=Path(
            os.getenv(
                "GATE_G1_REPORT_PATH",
                str(PROJECT_ROOT / "artifacts" / "gate_g1" / "gate_g1_benchmark_report.json"),
            )
        ),
        manifest=Path(
            os.getenv(
                "MODEL_MANIFEST_PATH",
                str(PROJECT_ROOT / "artifacts" / "models" / "plpt-logreg-v1.0.0" / "manifest.json"),
            )
        ),
        batch_error=health["batch_error"],
        drift_fallback_triggered=bool(
            health["drift_snapshot"]
            and health["drift_snapshot"].get("recommended_fallback_to_rules")
        ),
        drift_error=health["drift_error"],
    )
    return (
        route,
        health["latest_batch"],
        health["drift_snapshot"],
        health["drift_error"],
    )


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
        queue = build_queue(enquiries, now=now_vn, exclude_closed=True)

        active_queue = []
        for entry in queue:
            enq_id = str(entry.get("enquiry_id") or "").strip()
            acct_key = str(entry.get("account_key") or "").strip()
            if (enq_id and enq_id in blocked_enquiries) or (
                acct_key and acct_key in blocked_accounts
            ):
                continue
            active_queue.append(entry)

        is_stale = source_age_hours is None or source_age_hours > STALE_HOLD_HOURS
        status = STALE_STATUS if is_stale else "OK"
        if source_age_hours is None:
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
        user_id = auth.user_id if auth is not None else "PO_OPERATOR"
        return record_workspace_override(payload, user_id=user_id)
    except WorkspaceActionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.get("/shadow/summary")
@app.get("/api/v1/shadow/summary")
def get_shadow_summary_v1(
    k: int = Query(default=25, ge=1, le=500),
    _auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-65: Shadow summary, drift and fail-safe serving route."""
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {db_path}")
    summary = get_shadow_summary(db_path, k=k)
    route, latest_batch, drift_snapshot, drift_error = _resolve_shadow_route(db_path)
    summary["active_serving_mode"] = route.active_mode
    summary["fallback_reason"] = route.fallback_reason
    summary["fallback_triggered"] = route.fallback_triggered
    summary["route"] = route.to_dict()
    summary["status"] = "FALLBACK" if route.fallback_triggered else "OK"
    summary["latest_batch"] = latest_batch
    summary["drift"] = drift_snapshot
    summary["drift_error"] = drift_error
    reg_model = get_registered_model(db_path)
    if reg_model:
        summary["registered_model"] = {
            "model_version": reg_model.get("model_version"),
            "approval_status": reg_model.get("approval_status"),
            "training_row_count": reg_model.get("training_row_count"),
            "feature_dictionary_hash": reg_model.get("feature_dictionary_hash"),
        }
    return summary


@app.post("/shadow/fallback")
@app.post("/api/v1/shadow/fallback")
def post_shadow_fallback_v1(
    payload: dict[str, Any] | None = Body(default=None),
    auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-65: Return the detected automatic fallback or trigger a reasoned operator fallback."""
    payload = payload or {}
    db_path = get_db_path()
    if db_path.exists():
        route, latest_batch, drift_snapshot, drift_error = _resolve_shadow_route(
            db_path,
            requested_mode=PREDICTIVE_SERVING_MODE,
        )
        if route.fallback_triggered:
            return {
                "status": "FALLBACK_ACTIVE",
                "active_mode": route.active_mode,
                "fallback_triggered": True,
                "fallback_reason": route.fallback_reason,
                "triggered_by": auth.user_id if auth is not None else "MODEL_REVIEWER",
                "latest_batch": latest_batch,
                "drift": drift_snapshot,
                "drift_error": drift_error,
            }

    reason = str(payload.get("reason") or "MANUAL_OPERATOR_OVERRIDE").strip()
    user_id = auth.user_id if auth is not None else "MODEL_REVIEWER"
    decision = trigger_manual_fallback(reason=reason, triggered_by=user_id)
    return decision


@app.post("/api/v1/shadow/reset")
def post_shadow_reset_v1(
    _auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """Khôi phục trạng thái phục vụ tự động (tắt fallback thủ công)."""
    reset_manual_fallback()
    route = resolve_serving_route()
    return {
        "status": "RESET_SUCCESS",
        "active_mode": route.active_mode,
        "fallback_triggered": route.fallback_triggered,
        "fallback_reason": route.fallback_reason,
    }


@app.get("/api/v1/models/registry")
def get_models_registry_v1(
    _auth: AuthContext | None = Depends(
        require_roles({"MODEL_REVIEWER", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """MOD 08: Tra cứu danh sách các mô hình trong model_registry."""
    db_path = get_db_path()
    models = list_registered_models(db_path)
    return {"total": len(models), "models": models}

