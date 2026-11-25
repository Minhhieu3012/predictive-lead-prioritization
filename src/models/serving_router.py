"""PLPT-56: Rules-First Serving Router with Mandatory Automatic Fallback."""

from dataclasses import asdict, dataclass
import contextlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from src.monitoring.drift_monitor import compute_daily_monitoring_snapshot
from src.rules.rules_engine import RULES_LABEL, build_queue

DEFAULT_SERVING_MODE = RULES_LABEL
FALLBACK_SERVING_MODE = "FALLBACK_RULES_BASELINE"
PREDICTIVE_SERVING_MODE = "PREDICTIVE_ML"

_MANUAL_FALLBACK_ACTIVE = False
_MANUAL_FALLBACK_REASON: str | None = None
_MANUAL_FALLBACK_TRIGGERED_BY: str | None = None


def trigger_manual_fallback(
    reason: str,
    triggered_by: str = "MODEL_REVIEWER",
) -> dict[str, Any]:
    """
    Kích hoạt fallback thủ công từ MODEL_REVIEWER hoặc ADMINISTRATOR (Spec 09 Mục 4 & 5).
    Lập tức đưa hệ thống về RULES_BASELINE.
    """
    global _MANUAL_FALLBACK_ACTIVE, _MANUAL_FALLBACK_REASON, _MANUAL_FALLBACK_TRIGGERED_BY
    _MANUAL_FALLBACK_ACTIVE = True
    _MANUAL_FALLBACK_REASON = reason
    _MANUAL_FALLBACK_TRIGGERED_BY = triggered_by
    return {
        "status": "FALLBACK_ACTIVE",
        "active_mode": FALLBACK_SERVING_MODE,
        "fallback_triggered": True,
        "fallback_reason": reason,
        "triggered_by": triggered_by,
    }


def reset_manual_fallback() -> None:
    """Khôi phục trạng thái phục vụ tự động (tắt fallback thủ công)."""
    global _MANUAL_FALLBACK_ACTIVE, _MANUAL_FALLBACK_REASON, _MANUAL_FALLBACK_TRIGGERED_BY
    _MANUAL_FALLBACK_ACTIVE = False
    _MANUAL_FALLBACK_REASON = None
    _MANUAL_FALLBACK_TRIGGERED_BY = None


def get_manual_fallback_state() -> dict[str, Any]:
    return {
        "manual_fallback_active": _MANUAL_FALLBACK_ACTIVE,
        "fallback_reason": _MANUAL_FALLBACK_REASON,
        "triggered_by": _MANUAL_FALLBACK_TRIGGERED_BY,
    }


@dataclass(frozen=True)
class ServingRouteDecision:
    active_mode: str
    rules_only_enforced: bool
    fallback_triggered: bool
    fallback_reason: str | None
    model_version: str | None
    shadow_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json_if_path(source: dict[str, Any] | str | Path | None) -> dict[str, Any] | None:
    if source is None:
        return None
    if isinstance(source, dict):
        return source
    path = Path(source)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _configured_artifact(environment_key: str, relative_path: str) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(os.getenv(environment_key, str(project_root / relative_path)))


def get_serving_health(db_path: str | Path | None = None) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    path = Path(
        db_path
        or os.getenv("DATABASE_PATH", str(project_root / "data" / "songnhat.db"))
    )
    if not path.exists():
        return {
            "latest_batch": None,
            "batch_error": None,
            "drift_snapshot": None,
            "drift_error": f"DATABASE_NOT_FOUND: {path}",
        }

    latest_batch = None
    batch_error = None
    drift_snapshot = None
    drift_error = None
    try:
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            batch_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'batch_run_logs'"
            ).fetchone()
            if batch_table:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM batch_run_logs WHERE batch_type = ? ORDER BY id DESC LIMIT 1",
                    ("DAILY_SHADOW_REFRESH",),
                ).fetchone()
                latest_batch = dict(row) if row else None

            clean_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clean_enquiries'"
            ).fetchone()
            if clean_table is None:
                drift_error = "CLEAN_ENQUIRIES_TABLE_MISSING"
            else:
                enquiries = pd.read_sql_query("SELECT * FROM clean_enquiries", conn)

        if latest_batch:
            if latest_batch.get("status") != "SUCCESS":
                batch_error = str(
                    latest_batch.get("error_message")
                    or f"Batch status is {latest_batch.get('status')}"
                )
            elif not bool(latest_batch.get("sla_met")):
                batch_error = (
                    f"Batch exceeded SLA of {latest_batch.get('sla_threshold_seconds')} seconds"
                )

        if drift_error is None:
            reference_path = _configured_artifact(
                "DRIFT_REFERENCE_PATH",
                "artifacts/reference/drift_reference_baseline.json",
            )
            if not reference_path.exists():
                drift_error = "DRIFT_REFERENCE_MISSING"
            else:
                reference = json.loads(reference_path.read_text(encoding="utf-8"))
                drift_snapshot = compute_daily_monitoring_snapshot(
                    enquiries,
                    reference_missingness_pct=reference.get("reference_missingness_pct", {}),
                )
    except (sqlite3.Error, OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        drift_error = f"{type(error).__name__}: {error}"

    return {
        "latest_batch": latest_batch,
        "batch_error": batch_error,
        "drift_snapshot": drift_snapshot,
        "drift_error": drift_error,
    }


def get_active_scoring_mode() -> str:
    """
    Chỉ bật predictive khi báo cáo Gate G1 và manifest đều được xác minh/phê duyệt.
    """
    requested = os.getenv("ACTIVE_SCORING_MODE", DEFAULT_SERVING_MODE).strip().upper()
    health = (
        get_serving_health()
        if requested == PREDICTIVE_SERVING_MODE
        else {}
    )
    return resolve_serving_route(
        requested_mode=requested,
        gate_g1_report=_configured_artifact(
            "GATE_G1_REPORT_PATH",
            "artifacts/gate_g1/gate_g1_benchmark_report.json",
        ),
        manifest=_configured_artifact(
            "MODEL_MANIFEST_PATH",
            "artifacts/models/plpt-logreg-v1.0.0/manifest.json",
        ),
        batch_error=health.get("batch_error"),
        drift_fallback_triggered=bool(
            health.get("drift_snapshot")
            and health["drift_snapshot"].get("recommended_fallback_to_rules")
        ),
        drift_error=health.get("drift_error"),
    ).active_mode


def resolve_serving_route(
    *,
    requested_mode: str | None = None,
    gate_g1_report: dict[str, Any] | str | Path | None = None,
    manifest: dict[str, Any] | str | Path | None = None,
    control_health_ok: bool = True,
    drift_fallback_triggered: bool = False,
    drift_error: str | None = None,
    batch_error: str | None = None,
    incident_hold_active: bool = False,
    shadow_enabled: bool = True,
    insufficient_data: bool = False,
    coverage_below_90pct: bool = False,
    model_artifact_corrupt: bool = False,
) -> ServingRouteDecision:
    """
    Bộ định tuyến phục vụ theo chiến lược Rules-First (RFP Mục 9, PLPT-56 & Spec 09):
    - Mặc định luôn trả về `RULES_BASELINE`.
    - Tự động fallback về `FALLBACK_RULES_BASELINE` khi:
      + Kích hoạt ghi đè thủ công từ MODEL_REVIEWER hoặc ADMINISTRATOR
      + Sự cố khẩn cấp (incident_hold_active=True)
      + Kiểm soát bị gãy (control_health_ok=False)
      + Dữ liệu bị drift (drift_fallback_triggered=True)
      + Dữ liệu thiếu/không đủ độ chín (insufficient_data=True)
      + Độ bao phủ dưới 90% (coverage_below_90pct=True)
      + Artifact mô hình bị hỏng (model_artifact_corrupt=True)
      + Gate G1 chưa phê duyệt hoặc chỉ chạy trên dữ liệu SYNTHETIC
      + Manifest chưa mở khóa hoặc chưa APPROVED
    """
    try:
        report_data = _load_json_if_path(gate_g1_report)
    except (OSError, json.JSONDecodeError) as error:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"GATE_G1_REPORT_LOAD_FAILED: {error}",
            model_version=None,
            shadow_enabled=shadow_enabled,
        )
    try:
        manifest_data = _load_json_if_path(manifest)
    except (OSError, json.JSONDecodeError) as error:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"MODEL_MANIFEST_LOAD_FAILED: {error}",
            model_version=str(report_data.get("model_version")) if report_data else None,
            shadow_enabled=shadow_enabled,
        )
    model_version = (
        str(manifest_data.get("model_version"))
        if manifest_data and manifest_data.get("model_version")
        else (str(report_data.get("model_version")) if report_data and report_data.get("model_version") else None)
    )

    if _MANUAL_FALLBACK_ACTIVE:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"MANUAL_OVERRIDE_{_MANUAL_FALLBACK_REASON or 'MODEL_REVIEWER'}",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if incident_hold_active:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="INCIDENT_HOLD_ACTIVE",
            model_version=model_version,
            shadow_enabled=False,
        )

    if batch_error:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"SHADOW_BATCH_FAILED: {batch_error}",
            model_version=model_version,
            shadow_enabled=False,
        )

    if not control_health_ok:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="CONTROL_CHECK_FAILED",
            model_version=model_version,
            shadow_enabled=False,
        )

    if drift_fallback_triggered:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="DRIFT_THRESHOLD_BREACHED",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if drift_error:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"DRIFT_CHECK_FAILED: {drift_error}",
            model_version=model_version,
            shadow_enabled=False,
        )

    if insufficient_data:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="INSUFFICIENT_MATURE_DATA",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if coverage_below_90pct:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="COVERAGE_BELOW_90PCT",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if model_artifact_corrupt:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="MODEL_ARTIFACT_CORRUPT",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    req = (requested_mode or os.getenv("ACTIVE_SCORING_MODE", DEFAULT_SERVING_MODE)).strip().upper()

    if incident_hold_active:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="INCIDENT_HOLD_ACTIVE",
            model_version=model_version,
            shadow_enabled=False,
        )

    if not control_health_ok:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="CONTROL_CHECK_FAILED",
            model_version=model_version,
            shadow_enabled=False,
        )

    if drift_fallback_triggered:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="DRIFT_THRESHOLD_BREACHED",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if req != PREDICTIVE_SERVING_MODE:
        return ServingRouteDecision(
            active_mode=DEFAULT_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=False,
            fallback_reason=None,
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    if report_data is None:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="GATE_G1_REPORT_MISSING_OR_NOT_APPROVED",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )
    else:
        g1_decision = str(report_data.get("gate_g1_decision", "")).strip().upper()
        data_origin = str(report_data.get("data_origin", "SYNTHETIC")).strip().upper()
        if g1_decision != "APPROVED_PREDICTIVE_ML" or data_origin != "REAL":
            failed_criteria = report_data.get("failed_criteria") or []
            reason = f"GATE_G1_{g1_decision or 'UNVERIFIED'}_{data_origin}"
            if failed_criteria:
                reason = f"{reason}: FAILED_CRITERIA_{','.join(map(str, failed_criteria))}"
            return ServingRouteDecision(
                active_mode=FALLBACK_SERVING_MODE,
                rules_only_enforced=True,
                fallback_triggered=True,
                fallback_reason=reason,
                model_version=model_version,
                shadow_enabled=shadow_enabled,
            )

    if manifest_data is None:
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason="MODEL_MANIFEST_MISSING",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    locked = str(manifest_data.get("serving_mode_locked", DEFAULT_SERVING_MODE)).strip().upper()
    approval = str(manifest_data.get("approval_status", "")).strip().upper()
    if locked == DEFAULT_SERVING_MODE or approval != "APPROVED":
        return ServingRouteDecision(
            active_mode=FALLBACK_SERVING_MODE,
            rules_only_enforced=True,
            fallback_triggered=True,
            fallback_reason=f"MANIFEST_LOCKED_{locked}_{approval or 'UNAPPROVED'}",
            model_version=model_version,
            shadow_enabled=shadow_enabled,
        )

    return ServingRouteDecision(
        active_mode=PREDICTIVE_SERVING_MODE,
        rules_only_enforced=False,
        fallback_triggered=False,
        fallback_reason=None,
        model_version=model_version,
        shadow_enabled=shadow_enabled,
    )


def route_queue_scoring(
    enquiries: list[dict[str, Any]],
    *,
    now=None,
    exclude_closed: bool = True,
    route_decision: ServingRouteDecision | None = None,
    shadow_scores_by_id: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Định tuyến chấm điểm hàng đợi: luôn dùng `rules_engine.build_queue` làm trục chính Rules-First
    khi ở `RULES_BASELINE` hoặc `FALLBACK_RULES_BASELINE`; điểm shadow (nếu có) chỉ lưu tham chiếu ngầm.
    """
    decision = route_decision or resolve_serving_route()
    ranked = build_queue(enquiries, now=now, exclude_closed=exclude_closed)

    for item in ranked:
        item["serving_mode"] = decision.active_mode
        item["fallback_triggered"] = decision.fallback_triggered
        item["fallback_reason"] = decision.fallback_reason
        if shadow_scores_by_id and decision.shadow_enabled:
            enq_id = str(item.get("enquiry_id") or "")
            if enq_id in shadow_scores_by_id:
                item["shadow_score_logged"] = float(shadow_scores_by_id[enq_id])

    return {
        "route": decision.to_dict(),
        "items": ranked,
    }
