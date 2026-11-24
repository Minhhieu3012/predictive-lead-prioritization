"""PLPT-72 / OPS-06: Monitoring & Drift API wrapping src/monitoring/drift_monitor.py."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
import pandas as pd

from src.monitoring.drift_monitor import (
    MIN_SCORE_COVERAGE_PCT,
    MISSINGNESS_RISE_THRESHOLD_PP,
    assess_mature_ranking_drift,
    compute_daily_monitoring_snapshot,
    load_reference_baseline,
    persist_monitoring_snapshot,
)

VN_TZ = timezone(timedelta(hours=7))
MONITORING_ALLOWED_ROLES = {"MODEL_REVIEWER", "DATA_STEWARD", "SUPERVISOR", "ADMINISTRATOR"}

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


def _resolve_db_path() -> Path:
    env_path = os.getenv("DATABASE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return Path("data/lead_prioritization.db")


def _verify_monitoring_role(
    x_user_role: str | None = Header(default="MODEL_REVIEWER", alias="X-User-Role"),
    x_user_id: str | None = Header(default="system_reviewer", alias="X-User-Id"),
) -> dict[str, str]:
    role = (x_user_role or "").strip().upper()
    if role not in MONITORING_ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not authorized to access monitoring drift endpoints.",
        )
    return {"role": role, "user_id": (x_user_id or "anonymous").strip()}


def _load_table_df(
    db_path: Path, table_name: str, default_cols: list[str] | None = None
) -> pd.DataFrame:
    empty_df = pd.DataFrame(columns=default_cols or [])
    if not db_path.exists():
        return empty_df
    with sqlite3.connect(str(db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if not exists:
            return empty_df
        return pd.read_sql_query(f"SELECT * FROM {table_name}", conn)


@router.get("/drift")
def get_monitoring_drift(
    _auth: dict[str, str] = Depends(_verify_monitoring_role),
) -> dict[str, Any]:
    """
    OPS-06 / PLPT-72: Endpoint giám sát hằng ngày (khối lượng, missingness >= +10pp,
    coverage < 90%) và trạng thái chờ nhãn 60 ngày (DEFERRED_UNTIL_60D_MATURITY).
    Khóa cứng auto_retrain_enabled = False (cấm tự retrain).
    """
    now_vn = datetime.now(VN_TZ)
    db_path = _resolve_db_path()
    enquiries_df = _load_table_df(db_path, "clean_enquiries")
    outcomes_df = _load_table_df(db_path, "outcome_events", default_cols=["enquiry_id", "label"])

    if enquiries_df.empty:
        return {
            "status": "NO_DATA",
            "checked_at": now_vn.replace(microsecond=0).isoformat(),
            "thresholds": {
                "missingness_rise_threshold_pp": MISSINGNESS_RISE_THRESHOLD_PP,
                "min_score_coverage_pct": MIN_SCORE_COVERAGE_PCT,
            },
            "drift_detected": False,
            "investigation_triggered": False,
            "recommended_fallback_to_rules": False,
            "auto_retrain_allowed": False,
            "auto_retrain_enabled": False,
            "accuracy_assessment_status": "DEFERRED_UNTIL_60D_MATURITY",
            "daily_snapshot": {
                "as_of_date": now_vn.date().isoformat(),
                "daily_volume": 0,
                "score_coverage_pct": 0.0,
                "missingness_pct": {},
                "missingness_table": [],
                "channel_mix_pct": {},
                "alerts": [],
            },
            "mature_drift": {
                "status": "DEFERRED_UNTIL_60D_MATURITY",
                "mature_eligible_count": 0,
                "immature_excluded_count": 0,
                "known_mature_count": 0,
                "unknown_mature_count": 0,
                "mature_label_coverage_pct": 0.0,
                "ranking_drift_assessable": False,
                "reason": "DEFERRED_UNTIL_60D_MATURITY",
                "auto_retrain_allowed": False,
                "auto_retrain_enabled": False,
            },
            "alerts": [],
        }

    ref_missingness = load_reference_baseline(list(enquiries_df.columns))
    snapshot = compute_daily_monitoring_snapshot(
        enquiries_df,
        reference_missingness_pct=ref_missingness,
        as_of_date=now_vn.date().isoformat(),
    )
    mature_report = assess_mature_ranking_drift(
        enquiries_df,
        outcomes_df,
        as_of_dt=now_vn,
    )

    alerts = list(snapshot.get("alerts", []))
    drift_detected = bool(snapshot.get("investigation_triggered", False))
    acc_status = mature_report.get("status", "DEFERRED_UNTIL_60D_MATURITY")

    persist_monitoring_snapshot(db_path, snapshot, alerts)

    return {
        "status": "DRIFT_DETECTED" if drift_detected else "HEALTHY",
        "checked_at": now_vn.replace(microsecond=0).isoformat(),
        "thresholds": {
            "missingness_rise_threshold_pp": MISSINGNESS_RISE_THRESHOLD_PP,
            "min_score_coverage_pct": MIN_SCORE_COVERAGE_PCT,
        },
        "drift_detected": drift_detected,
        "investigation_triggered": drift_detected,
        "recommended_fallback_to_rules": bool(
            snapshot.get("recommended_fallback_to_rules", drift_detected)
        ),
        "auto_retrain_allowed": False,
        "auto_retrain_enabled": False,
        "accuracy_assessment_status": acc_status,
        "daily_snapshot": snapshot,
        "mature_drift": mature_report,
        "alerts": alerts,
    }
