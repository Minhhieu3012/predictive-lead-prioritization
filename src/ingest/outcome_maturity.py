"""PLPT-27 / CF-14..CF-18: 60-Day Outcome Maturity Evaluation & Versioned Label Correction."""

from datetime import datetime, timedelta, timezone
import contextlib
import sqlite3
from typing import Any

VN_TZ = timezone(timedelta(hours=7))
MATURE_HORIZON_DAYS = 60


def evaluate_60d_outcome_label(
    *,
    t0_time: str,
    observation_time: str,
    business_need_confirmed: bool,
    product_fit_confirmed: bool,
    agreed_next_step_confirmed: bool,
    evidence_checklist_passed: bool,
    observer_coverage: str = "FULL",
    crm_stage: str | None = None,
) -> dict[str, Any]:
    """
    Đánh giá nhãn mục tiêu 60 ngày dương lịch theo đúng Rubric 3 điều kiện (us_02 & CF-14..CF-17):
    - Chỉ gán POSITIVE khi đủ cả 3 tiêu chí + evidence_checklist_passed=True trong <= 60 ngày.
    - Chuyển CRM stage sang "Qualified" nhưng evidence_checklist_passed=False -> không được gán POSITIVE.
    - Đủ > 60 ngày với observer_coverage='FULL' và không có sự kiện đạt chuẩn -> NEGATIVE.
    - Chưa đủ 60 ngày và chưa đạt chuẩn -> Unknown (IMMATURE).
    """
    t0_dt = datetime.fromisoformat(t0_time.replace("Z", "+00:00"))
    obs_dt = datetime.fromisoformat(observation_time.replace("Z", "+00:00"))
    if t0_dt.tzinfo is None:
        t0_dt = t0_dt.replace(tzinfo=VN_TZ)
    if obs_dt.tzinfo is None:
        obs_dt = obs_dt.replace(tzinfo=VN_TZ)

    elapsed_days = (obs_dt - t0_dt).total_seconds() / 86400.0
    all_three = bool(business_need_confirmed and product_fit_confirmed and agreed_next_step_confirmed)

    if all_three and evidence_checklist_passed and elapsed_days <= MATURE_HORIZON_DAYS:
        return {
            "label": "POSITIVE",
            "maturity_state": "MATURE",
            "elapsed_days": round(elapsed_days, 2),
            "reason": "Evidenced 3/3 rubric criteria within 60 calendar days",
        }

    if elapsed_days >= MATURE_HORIZON_DAYS and str(observer_coverage).strip().upper() == "FULL":
        return {
            "label": "NEGATIVE",
            "maturity_state": "MATURE",
            "elapsed_days": round(elapsed_days, 2),
            "reason": "Full 60-day horizon elapsed with complete observation and no qualifying event",
        }

    reason = "Immature horizon (< 60 days) without full evidenced qualification"
    if crm_stage and crm_stage.strip().lower() == "qualified" and not evidence_checklist_passed:
        reason = "CRM stage Qualified rejected as POSITIVE because evidence_checklist_passed=False"

    return {
        "label": "Unknown",
        "maturity_state": "IMMATURE",
        "elapsed_days": round(elapsed_days, 2),
        "reason": reason,
    }


def apply_versioned_label_correction(
    db_path: str,
    *,
    enquiry_id: str,
    new_label: str,
    recorded_at: str,
    correction_reason: str,
    prior_frozen_score: float | None = None,
) -> dict[str, Any]:
    """
    CF-18: Ghi nhận hiệu chỉnh nhãn có phiên bản (versioned label revision)
    vào bảng `outcome_label_revisions` mà không ghi đè âm thầm điểm lịch sử đã đóng băng.
    """
    norm_label = new_label.strip().upper()
    if norm_label == "UNKNOWN":
        norm_label = "Unknown"
    if norm_label not in {"POSITIVE", "NEGATIVE", "Unknown"}:
        raise ValueError(f"Invalid label: {new_label}")

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outcome_label_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enquiry_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    correction_reason TEXT NOT NULL,
                    preserved_historical_score REAL,
                    created_at TEXT NOT NULL
                )
                """
            )
            prev_rev = conn.execute(
                "SELECT COALESCE(MAX(revision_number), 0) FROM outcome_label_revisions WHERE enquiry_id = ?",
                (enquiry_id,),
            ).fetchone()[0]
            next_rev = int(prev_rev) + 1
            now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
            conn.execute(
                """
                INSERT INTO outcome_label_revisions (
                    enquiry_id, revision_number, label, recorded_at,
                    correction_reason, preserved_historical_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (enquiry_id, next_rev, norm_label, recorded_at, correction_reason, prior_frozen_score, now_iso),
            )
            conn.commit()

    return {
        "enquiry_id": enquiry_id,
        "revision_number": next_rev,
        "label": norm_label,
        "recorded_at": recorded_at,
        "preserved_historical_score": prior_frozen_score,
    }

