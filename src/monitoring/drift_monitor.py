"""Technical Spec 11 (OPS 06, OPS 08): Daily Data Drift, Missingness & Coverage Monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

MISSINGNESS_RISE_THRESHOLD_PP = 10.0
MIN_SCORE_COVERAGE_PCT = 90.0
MATURITY_DAYS = 60

APPROVED_PRE_T0_FEATURES = [
    "spec_completeness",
    "contract_due_date",
    "industry_at_t0",
    "account_age_days_at_t0",
    "prior_purchase_count_at_t0",
    "product_family",
]

DEFAULT_BASELINE_PATH = Path("artifacts/reference/drift_reference_baseline.json")


def load_reference_baseline(
    columns: list[str] | None = None,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
) -> dict[str, float]:
    """Đọc reference baseline được phê duyệt; fallback 0.0 nếu chưa có file."""
    base: dict[str, float] = {}
    if baseline_path.exists():
        with open(baseline_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            ref = data.get("feature_missing_pct", {})
            if isinstance(ref, dict):
                base = {str(k): float(v) for k, v in ref.items()}
    for col in (columns or APPROVED_PRE_T0_FEATURES):
        base.setdefault(col, 0.0)
    return base


def _compute_col_missing_pct(series: pd.Series) -> float:
    if len(series) == 0:
        return 0.0
    is_null = series.isna()
    stripped = series.fillna("").astype(str).str.strip().str.lower()
    is_null = is_null | stripped.isin(["", "null", "none", "nan", "unknown"])
    return round(float(is_null.mean() * 100.0), 2)


def persist_monitoring_snapshot(
    db_path: Path,
    snapshot: dict[str, Any],
    alerts: list[dict[str, Any]],
) -> None:
    """Lưu lịch sử snapshot & investigation alerts vào SQLite (OPS 06)."""
    if not db_path.exists():
        return
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_drift_snapshots (
                snapshot_date TEXT PRIMARY KEY,
                daily_volume INTEGER NOT NULL,
                score_coverage_pct REAL NOT NULL,
                missingness_json TEXT NOT NULL,
                channel_mix_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drift_investigation_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                alert_code TEXT NOT NULL,
                feature_name TEXT,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at TEXT NOT NULL
            );
            """
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_drift_snapshots
            (snapshot_date, daily_volume, score_coverage_pct, missingness_json, channel_mix_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["as_of_date"],
                int(snapshot["daily_volume"]),
                float(snapshot["score_coverage_pct"]),
                json.dumps(snapshot.get("missingness_table", [])),
                json.dumps(snapshot.get("channel_mix_pct", {})),
                now_iso,
            ),
        )
        conn.execute(
            "DELETE FROM drift_investigation_alerts WHERE snapshot_date = ?",
            (snapshot["as_of_date"],),
        )
        for alert in alerts:
            conn.execute(
                """
                INSERT INTO drift_investigation_alerts
                (snapshot_date, alert_code, feature_name, message, status, created_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    snapshot["as_of_date"],
                    alert["code"],
                    alert.get("feature"),
                    alert["message"],
                    now_iso,
                ),
            )
        conn.commit()


def compute_daily_monitoring_snapshot(
    enquiries_df: pd.DataFrame,
    reference_missingness_pct: dict[str, float] | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Tính toán daily volume, per-feature missingness delta, coverage và channel mix."""
    snap_date = as_of_date or datetime.now(timezone.utc).date().isoformat()
    total_rows = int(len(enquiries_df))

    if total_rows == 0:
        return {
            "as_of_date": snap_date,
            "daily_volume": 0,
            "score_coverage_pct": 0.0,
            "unscored_rate_pct": 100.0,
            "missingness_pct": {},
            "missingness_table": [],
            "channel_mix_pct": {},
            "investigation_triggered": False,
            "recommended_fallback_to_rules": False,
            "alerts": [],
            "auto_retrain_enabled": False,
            "accuracy_assessment_status": "DEFERRED_UNTIL_60D_MATURITY",
        }

    ref_map = reference_missingness_pct or load_reference_baseline(list(enquiries_df.columns))
    target_cols = [c for c in APPROVED_PRE_T0_FEATURES if c in enquiries_df.columns]
    if not target_cols:
        target_cols = [
            c for c in enquiries_df.columns
            if c not in {"enquiry_id", "account_key", "cohort_id", "owner_id", "t0_time", "extraction_time", "workflow_state"}
        ]

    missingness_pct: dict[str, float] = {}
    missingness_table: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    for col in target_cols:
        curr_pct = _compute_col_missing_pct(enquiries_df[col])
        ref_pct = float(ref_map.get(col, 0.0))
        delta_pp = round(curr_pct - ref_pct, 2)
        breached = delta_pp >= MISSINGNESS_RISE_THRESHOLD_PP

        missingness_pct[col] = curr_pct
        missingness_table.append({
            "feature": col,
            "baseline_missing_pct": ref_pct,
            "current_missing_pct": curr_pct,
            "delta_pp": delta_pp,
            "is_drift_breach": breached,
        })

        if breached:
            alerts.append({
                "code": "MISSINGNESS_SPIKE",
                "spec_code": "MISSINGNESS_DRIFT_10PP",
                "feature": col,
                "delta_pp": delta_pp,
                "threshold_pp": MISSINGNESS_RISE_THRESHOLD_PP,
                "message": f"Feature '{col}' thiếu dữ liệu tăng +{delta_pp} pp (>= {MISSINGNESS_RISE_THRESHOLD_PP} pp).",
            })

    # Tính Score Coverage
    unscored_mask = pd.Series(False, index=enquiries_df.index)
    if "workflow_state" in enquiries_df.columns:
        ws = enquiries_df["workflow_state"].fillna("").astype(str).str.upper()
        unscored_mask = unscored_mask | ws.isin(["UNSCORED", "QUARANTINED"])
    if "spec_completeness" in enquiries_df.columns:
        sc = enquiries_df["spec_completeness"].fillna("").astype(str).str.strip().str.lower()
        unscored_mask = unscored_mask | sc.isin(["", "null", "none", "nan", "unknown"])

    scored_count = int((~unscored_mask).sum())
    score_coverage_pct = round((scored_count / total_rows) * 100.0, 2)
    unscored_rate_pct = round(100.0 - score_coverage_pct, 2)

    if score_coverage_pct < MIN_SCORE_COVERAGE_PCT:
        alerts.append({
            "code": "LOW_COVERAGE",
            "spec_code": "COVERAGE_BELOW_90PCT",
            "score_coverage_pct": score_coverage_pct,
            "threshold_pct": MIN_SCORE_COVERAGE_PCT,
            "message": f"Score coverage giảm còn {score_coverage_pct}% (< ngưỡng tối thiểu {MIN_SCORE_COVERAGE_PCT}%).",
        })

    # Phân bổ kênh thu hút (Channel Mix)
    channel_mix_pct: dict[str, float] = {}
    channel_col = "enquiry_channel_at_t0" if "enquiry_channel_at_t0" in enquiries_df.columns else (
        "product_family" if "product_family" in enquiries_df.columns else None
    )
    if channel_col:
        vc = enquiries_df[channel_col].fillna("Unknown").replace("", "Unknown").value_counts(normalize=True) * 100.0
        channel_mix_pct = {str(k): round(float(v), 2) for k, v in vc.items()}

    investigation_triggered = len(alerts) > 0

    return {
        "as_of_date": snap_date,
        "daily_volume": total_rows,
        "score_coverage_pct": score_coverage_pct,
        "unscored_rate_pct": unscored_rate_pct,
        "missingness_pct": missingness_pct,
        "missingness_table": missingness_table,
        "channel_mix_pct": channel_mix_pct,
        "investigation_triggered": investigation_triggered,
        "recommended_fallback_to_rules": investigation_triggered,
        "alerts": alerts,
        "auto_retrain_enabled": False,
        "auto_retrain_allowed": False,
        "accuracy_assessment_status": "DEFERRED_UNTIL_60D_MATURITY",
    }


def assess_mature_ranking_drift(
    enquiries_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    as_of_dt: datetime | None = None,
) -> dict[str, Any]:
    """Trì hoãn đánh giá ranking accuracy cho đến khi đủ 60 ngày dương lịch (OPS 06)."""
    now_dt = as_of_dt or datetime.now(timezone.utc)
    if enquiries_df.empty or "t0_time" not in enquiries_df.columns:
        return {
            "status": "DEFERRED_UNTIL_60D_MATURITY",
            "mature_eligible_count": 0,
            "immature_excluded_count": int(len(enquiries_df)),
            "known_mature_count": 0,
            "unknown_mature_count": 0,
            "mature_label_coverage_pct": 0.0,
            "ranking_drift_assessable": False,
            "reason": "DEFERRED_UNTIL_60D_MATURITY",
            "auto_retrain_allowed": False,
            "auto_retrain_enabled": False,
        }

    t0_parsed = pd.to_datetime(enquiries_df["t0_time"], errors="coerce", utc=True)
    cutoff = pd.Timestamp(now_dt).tz_convert("UTC") - timedelta(days=MATURITY_DAYS)
    mature_mask = t0_parsed.notna() & (t0_parsed <= cutoff)

    mature_enq = enquiries_df[mature_mask]
    immature_count = int((~mature_mask).sum())
    mature_count = int(len(mature_enq))

    if mature_count == 0 or outcomes_df.empty or "enquiry_id" not in outcomes_df.columns:
        return {
            "status": "DEFERRED_UNTIL_60D_MATURITY",
            "mature_eligible_count": mature_count,
            "immature_excluded_count": immature_count,
            "known_mature_count": 0,
            "unknown_mature_count": mature_count,
            "mature_label_coverage_pct": 0.0,
            "ranking_drift_assessable": False,
            "reason": "DEFERRED_UNTIL_60D_MATURITY",
            "auto_retrain_allowed": False,
            "auto_retrain_enabled": False,
        }

    merged = mature_enq.merge(outcomes_df, on="enquiry_id", how="left")
    known_count = int(merged["label"].notna().sum()) if "label" in merged.columns else 0
    unknown_count = mature_count - known_count
    label_cov = round((known_count / mature_count) * 100.0, 2)

    return {
        "status": "READY" if known_count > 0 else "DEFERRED_UNTIL_60D_MATURITY",
        "mature_eligible_count": mature_count,
        "immature_excluded_count": immature_count,
        "known_mature_count": known_count,
        "unknown_mature_count": unknown_count,
        "mature_label_coverage_pct": label_cov,
        "ranking_drift_assessable": known_count > 0,
        "reason": "MATURE_60D_LABELS_AVAILABLE" if known_count > 0 else "DEFERRED_UNTIL_60D_MATURITY",
        "auto_retrain_allowed": False,
        "auto_retrain_enabled": False,
    }
