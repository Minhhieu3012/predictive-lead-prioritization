"""PLPT-58 / OPS-06: Daily Volume, Missingness, Coverage & Mature Drift Monitor."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))
MISSINGNESS_RISE_THRESHOLD_PP = 10.0  # +10 điểm phần trăm so với mốc tham chiếu
MIN_SCORE_COVERAGE_PCT = 90.0         # Ngưỡng độ bao phủ chấm điểm tối thiểu 90%
MATURE_HORIZON_DAYS = 60

DEFAULT_MONITORED_COLUMNS = (
    "product_family",
    "spec_completeness",
    "is_urgent_replacement",
    "contract_due_date",
    "industry_at_t0",
    "enquiry_channel_at_t0",
    "region_at_t0",
    "estimated_value_band_at_t0",
)


def _is_missing_series(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().isin({"", "none", "None", "null", "NULL", "nan", "NaN"})


def compute_daily_monitoring_snapshot(
    df: pd.DataFrame,
    *,
    reference_missingness_pct: dict[str, float] | None = None,
    monitored_columns: tuple[str, ...] = DEFAULT_MONITORED_COLUMNS,
    scored_column: str | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """
    OPS-06: Giám sát hằng ngày khối lượng, tỷ lệ thiếu dữ liệu, độ bao phủ chấm điểm và cơ cấu nguồn.
    - Ngưỡng cảnh báo: tỷ lệ thiếu tăng >= 10 điểm phần trăm so với tham chiếu hoặc độ bao phủ < 90%.
    - Quy tắc RFP: ngưỡng cảnh báo là tín hiệu điều tra (investigation trigger), tuyệt đối không tự động
      huấn luyện lại (`auto_retrain_allowed = False`); khuyến nghị quay về Rules Baseline nếu vượt ngưỡng.
    """
    snapshot_date = as_of_date or datetime.now(VN_TZ).date().isoformat()
    total_rows = int(len(df))
    ref = reference_missingness_pct or {}

    if total_rows == 0:
        return {
            "as_of_date": snapshot_date,
            "daily_volume": 0,
            "missingness_pct": {},
            "missingness_delta_pp": {},
            "score_coverage_pct": 0.0,
            "unscored_count": 0,
            "source_mix_pct": {},
            "alerts": [
                {
                    "code": "VOLUME_ZERO",
                    "detail": "Khối lượng hồ sơ trong ngày bằng 0; cần kiểm tra pipeline import.",
                }
            ],
            "investigation_triggered": True,
            "recommended_fallback_to_rules": True,
            "auto_retrain_allowed": False,
        }

    missingness_pct: dict[str, float] = {}
    missingness_delta_pp: dict[str, float] = {}
    alerts: list[dict[str, Any]] = []

    for col in monitored_columns:
        if col not in df.columns:
            continue
        miss_pct = round(float(_is_missing_series(df[col]).mean() * 100.0), 2)
        missingness_pct[col] = miss_pct
        if col in ref:
            delta = round(miss_pct - float(ref[col]), 2)
            missingness_delta_pp[col] = delta
            if delta >= MISSINGNESS_RISE_THRESHOLD_PP:
                alerts.append(
                    {
                        "code": "MISSINGNESS_SPIKE",
                        "column": col,
                        "current_pct": miss_pct,
                        "reference_pct": float(ref[col]),
                        "delta_pp": delta,
                        "detail": f"Cột {col} tăng tỷ lệ thiếu +{delta:.2f} điểm phần trăm (>= {MISSINGNESS_RISE_THRESHOLD_PP}pp).",
                    }
                )

    if scored_column and scored_column in df.columns:
        unscored_mask = _is_missing_series(df[scored_column])
    elif "spec_completeness" in df.columns:
        # Theo quy ước Workspace: thiếu/Minimal thông số bị coi là unscored hoặc thiếu dữ liệu đầu vào
        unscored_mask = _is_missing_series(df["spec_completeness"])
    else:
        unscored_mask = pd.Series([False] * total_rows, index=df.index)

    unscored_count = int(unscored_mask.sum())
    score_coverage_pct = round(float((total_rows - unscored_count) / total_rows * 100.0), 2)

    if score_coverage_pct < MIN_SCORE_COVERAGE_PCT:
        alerts.append(
            {
                "code": "LOW_COVERAGE",
                "score_coverage_pct": score_coverage_pct,
                "threshold_pct": MIN_SCORE_COVERAGE_PCT,
                "detail": f"Độ bao phủ chấm điểm {score_coverage_pct:.2f}% dưới ngưỡng {MIN_SCORE_COVERAGE_PCT:.0f}%.",
            }
        )

    source_col = "source" if "source" in df.columns else ("enquiry_channel_at_t0" if "enquiry_channel_at_t0" in df.columns else None)
    source_mix_pct: dict[str, float] = {}
    if source_col is not None:
        cleaned_src = df[source_col].fillna("UNKNOWN").astype(str).str.strip().replace("", "UNKNOWN")
        counts = cleaned_src.value_counts(normalize=True) * 100.0
        source_mix_pct = {str(k): round(float(v), 2) for k, v in counts.items()}

    investigation_triggered = len(alerts) > 0
    return {
        "as_of_date": snapshot_date,
        "daily_volume": total_rows,
        "missingness_pct": missingness_pct,
        "missingness_delta_pp": missingness_delta_pp,
        "score_coverage_pct": score_coverage_pct,
        "unscored_count": unscored_count,
        "source_mix_pct": source_mix_pct,
        "alerts": alerts,
        "investigation_triggered": investigation_triggered,
        "recommended_fallback_to_rules": investigation_triggered,
        "auto_retrain_allowed": False,
    }


def assess_mature_ranking_drift(
    enquiries_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    *,
    as_of_dt: datetime | None = None,
    min_mature_coverage_pct: float = MIN_SCORE_COVERAGE_PCT,
) -> dict[str, Any]:
    """
    OPS-06: Chỉ được phép đánh giá trôi dạt chất lượng xếp hạng SAU KHI nhãn đã chín đủ 60 ngày dương lịch.
    Bắt buộc công bố số lượng hồ sơ chưa trưởng thành (`Unknown`) và từ chối kết luận nếu thiếu độ phủ.
    """
    now_dt = as_of_dt or datetime.now(VN_TZ)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=VN_TZ)

    if enquiries_df.empty:
        return {
            "mature_eligible_count": 0,
            "immature_excluded_count": 0,
            "known_mature_count": 0,
            "unknown_mature_count": 0,
            "mature_label_coverage_pct": 0.0,
            "ranking_drift_assessable": False,
            "reason": "EMPTY_COHORT",
            "auto_retrain_allowed": False,
        }

    merged = enquiries_df.merge(outcomes_df, on="enquiry_id", how="left", suffixes=("", "_out"))
    t0_parsed = pd.to_datetime(merged["t0_time"], errors="coerce", utc=True)
    now_utc = pd.Timestamp(now_dt).tz_convert("UTC")
    elapsed_days = (now_utc - t0_parsed).dt.total_seconds() / 86400.0

    mature_mask = elapsed_days >= MATURE_HORIZON_DAYS
    mature_df = merged[mature_mask].copy()
    immature_count = int((~mature_mask).sum())

    labels = mature_df.get("label", pd.Series(dtype=str)).fillna("Unknown").astype(str).str.strip().str.upper()
    known_mask = labels.isin({"POSITIVE", "NEGATIVE"})
    known_count = int(known_mask.sum())
    unknown_count = int(len(mature_df) - known_count)
    coverage_pct = round((known_count / len(mature_df) * 100.0), 2) if len(mature_df) else 0.0

    assessable = len(mature_df) > 0 and coverage_pct >= min_mature_coverage_pct
    positive_rate = round(float((labels[known_mask] == "POSITIVE").mean()), 4) if known_count else None

    return {
        "mature_eligible_count": int(len(mature_df)),
        "immature_excluded_count": immature_count,
        "known_mature_count": known_count,
        "unknown_mature_count": unknown_count,
        "mature_label_coverage_pct": coverage_pct,
        "observed_positive_rate": positive_rate,
        "ranking_drift_assessable": assessable,
        "reason": None if assessable else "INSUFFICIENT_MATURE_LABELS_OR_COVERAGE",
        "auto_retrain_allowed": False,
    }



def ensure_drift_tables(conn) -> None:
    """PLPT-71: Tạo bảng daily_drift_snapshots lưu kết quả giám sát trôi dạt hằng ngày."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_drift_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date TEXT NOT NULL,
            daily_volume INTEGER NOT NULL,
            score_coverage_pct REAL NOT NULL,
            unscored_count INTEGER NOT NULL,
            missingness_json TEXT NOT NULL,
            missingness_delta_json TEXT NOT NULL,
            source_mix_json TEXT NOT NULL,
            alerts_json TEXT NOT NULL,
            investigation_triggered INTEGER NOT NULL,
            recommended_fallback_to_rules INTEGER NOT NULL,
            auto_retrain_allowed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def persist_daily_drift_snapshot(db_path: str, snapshot: dict[str, Any]) -> int:
    """PLPT-71: Lưu snapshot giám sát trôi dạt hằng ngày vào bảng daily_drift_snapshots."""
    import contextlib, json, sqlite3
    created_at = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_drift_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO daily_drift_snapshots (
                    as_of_date, daily_volume, score_coverage_pct, unscored_count,
                    missingness_json, missingness_delta_json, source_mix_json, alerts_json,
                    investigation_triggered, recommended_fallback_to_rules,
                    auto_retrain_allowed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    str(snapshot["as_of_date"]),
                    int(snapshot["daily_volume"]),
                    float(snapshot["score_coverage_pct"]),
                    int(snapshot["unscored_count"]),
                    json.dumps(snapshot.get("missingness_pct", {}), ensure_ascii=False),
                    json.dumps(snapshot.get("missingness_delta_pp", {}), ensure_ascii=False),
                    json.dumps(snapshot.get("source_mix_pct", {}), ensure_ascii=False),
                    json.dumps(snapshot.get("alerts", []), ensure_ascii=False),
                    1 if snapshot.get("investigation_triggered") else 0,
                    1 if snapshot.get("recommended_fallback_to_rules") else 0,
                    created_at,
                ),
            )
            row_id = int(cur.lastrowid)
            conn.commit()
            return row_id
