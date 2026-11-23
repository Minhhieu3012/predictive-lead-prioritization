"""PLPT-72 / OPS-06: Monitoring & Drift API wrapping src/monitoring/drift_monitor.py."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends
import pandas as pd

from src.monitoring.drift_monitor import (
    MIN_SCORE_COVERAGE_PCT,
    MISSINGNESS_RISE_THRESHOLD_PP,
    assess_mature_ranking_drift,
    compute_daily_monitoring_snapshot,
)
from src.security.rbac import (
    VALID_ROLES,
    AuthContext,
    is_rbac_enforced_on_v1,
    require_roles,
)
from src.services.queue_service import get_db_path

VN_TZ = timezone(timedelta(hours=7))

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


def _resolve_db_path() -> Path:
    env_path = os.getenv("DATABASE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return get_db_path()


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
    _auth: AuthContext | None = Depends(
        require_roles(VALID_ROLES, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """
    OPS-06 / PLPT-72: Endpoint giám sát hằng ngày (khối lượng, missingness >= +10pp,
    coverage < 90%) và đánh giá mature ranking drift (>= 60 ngày dương lịch).
    Tuyệt đối không tự động retrain (`auto_retrain_allowed = False`).
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
            "daily_snapshot": {
                "as_of_date": now_vn.date().isoformat(),
                "daily_volume": 0,
                "score_coverage_pct": 0.0,
                "missingness_pct": {},
                "alerts": [],
            },
            "mature_drift": {
                "mature_eligible_count": 0,
                "immature_excluded_count": 0,
                "known_mature_count": 0,
                "unknown_mature_count": 0,
                "mature_label_coverage_pct": 0.0,
                "ranking_drift_assessable": False,
                "reason": "EMPTY_COHORT",
                "auto_retrain_allowed": False,
            },
            "alerts": [],
        }

    ref_missingness = {col: 0.0 for col in enquiries_df.columns}
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
        "daily_snapshot": snapshot,
        "mature_drift": mature_report,
        "alerts": alerts,
    }