"""PLPT-57: Account-Level Pilot Assignment & 20-Business-Day Observed Effort Tracker."""

import contextlib
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from src.config.vn_holidays import is_vn_business_day

VN_TZ = timezone(timedelta(hours=7))
MIN_PILOT_BUSINESS_DAYS = 20
TARGET_EFFORT_REDUCTION_PCT = 20.0
DEFAULT_BASELINE_TRIAGE_MINUTES = 6.0


def assign_account_treatment(
    account_key: str,
    *,
    salt: str = "songnhat-pilot-v1",
    control_ratio: float = 0.5,
) -> str:
    """
    RFP Mục 10: Phân bổ nhóm thí điểm ở cấp độ tài khoản (`account_key`) bằng hàm băm tất định
    để đảm bảo các yêu cầu liên quan của cùng 1 khách hàng không nhận chế độ xử lý mâu thuẫn nhau.
    """
    acct = str(account_key or "").strip().upper()
    if not acct:
        raise ValueError("account_key không được rỗng khi phân bổ thí điểm.")
    if not (0.0 <= control_ratio <= 1.0):
        raise ValueError("control_ratio phải nằm trong [0.0, 1.0].")

    digest = hashlib.sha256(f"{salt}:{acct}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "RULES_CONTROL" if bucket < control_ratio else "SUPERVISED_TREATMENT"


def assign_cohort_by_account(
    enquiries_df: pd.DataFrame,
    *,
    salt: str = "songnhat-pilot-v1",
    control_ratio: float = 0.5,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Gắn nhãn `treatment_arm` nhất quán theo `account_key` và xác minh không xung đột tài khoản."""
    out = enquiries_df.copy()
    if out.empty:
        out["treatment_arm"] = pd.Series(dtype=str)
        return out, {"total_rows": 0, "unique_accounts": 0, "conflicting_accounts": 0, "arm_counts": {}}

    out["treatment_arm"] = out["account_key"].apply(
        lambda acct: assign_account_treatment(str(acct), salt=salt, control_ratio=control_ratio)
    )
    per_acct_arms = out.groupby("account_key")["treatment_arm"].nunique()
    conflicting = int((per_acct_arms > 1).sum())
    arm_counts = {str(k): int(v) for k, v in out["treatment_arm"].value_counts().to_dict().items()}

    return out, {
        "total_rows": int(len(out)),
        "unique_accounts": int(out["account_key"].nunique()),
        "conflicting_accounts": conflicting,
        "arm_counts": arm_counts,
    }


def ensure_pilot_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pilot_effort_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_date TEXT NOT NULL,
            staff_id TEXT NOT NULL,
            treatment_arm TEXT NOT NULL DEFAULT 'RULES_CONTROL',
            enquiries_reviewed INTEGER NOT NULL,
            queue_prep_minutes REAL NOT NULL,
            triage_minutes REAL NOT NULL,
            due_total INTEGER NOT NULL DEFAULT 0,
            due_on_time INTEGER NOT NULL DEFAULT 0,
            override_count INTEGER NOT NULL DEFAULT 0,
            logged_at TEXT NOT NULL
        )
        """
    )


def log_pilot_effort_session(
    db_path: str | Path,
    *,
    business_date: str | date,
    staff_id: str,
    enquiries_reviewed: int,
    queue_prep_minutes: float,
    triage_minutes: float,
    due_total: int = 0,
    due_on_time: int = 0,
    override_count: int = 0,
    treatment_arm: str = "RULES_CONTROL",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ghi nhận nhật ký công sức chuẩn bị hàng đợi & phân loại hàng ngày trong đợt thí điểm 20 ngày làm việc."""
    b_date = business_date if isinstance(business_date, date) else date.fromisoformat(str(business_date)[:10])
    if enquiries_reviewed < 0 or queue_prep_minutes < 0 or triage_minutes < 0:
        raise ValueError("Số lượng hồ sơ và thời gian xử lý phải >= 0.")
    if due_on_time > due_total:
        raise ValueError("due_on_time không được lớn hơn due_total.")

    logged_at = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_pilot_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO pilot_effort_logs (
                    business_date, staff_id, treatment_arm, enquiries_reviewed,
                    queue_prep_minutes, triage_minutes, due_total, due_on_time,
                    override_count, logged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b_date.isoformat(),
                    str(staff_id or "sales_staff").strip(),
                    str(treatment_arm or "RULES_CONTROL").strip().upper(),
                    int(enquiries_reviewed),
                    float(queue_prep_minutes),
                    float(triage_minutes),
                    int(due_total),
                    int(due_on_time),
                    int(override_count),
                    logged_at,
                ),
            )
            row_id = int(cur.lastrowid)
            conn.commit()

    return {
        "id": row_id,
        "business_date": b_date.isoformat(),
        "is_vn_business_day": is_vn_business_day(b_date),
        "staff_id": str(staff_id or "sales_staff").strip(),
        "enquiries_reviewed": int(enquiries_reviewed),
        "total_effort_minutes": round(float(queue_prep_minutes + triage_minutes), 2),
        "logged_at": logged_at,
    }


def summarize_pilot_20bd(
    db_path: str | Path,
    *,
    baseline_minutes_per_lead: float = DEFAULT_BASELINE_TRIAGE_MINUTES,
    baseline_on_time_rate: float | None = None,
) -> dict[str, Any]:
    """
    RFP Mục 8 (Observed effort): Tổng hợp kết quả giai đoạn sử dụng có giám sát >= 20 ngày làm việc,
    đối chiếu mục tiêu giảm >= 20% công sức chuẩn bị/phân loại hàng đợi và không làm tệ hơn tỷ lệ đúng hạn.
    """
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_pilot_tables(conn)
            df = pd.read_sql_query("SELECT * FROM pilot_effort_logs", conn)

    if df.empty:
        return {
            "observed_business_days": 0,
            "min_business_days_required": MIN_PILOT_BUSINESS_DAYS,
            "min_20bd_met": False,
            "total_enquiries_reviewed": 0,
            "avg_minutes_per_lead": None,
            "baseline_minutes_per_lead": baseline_minutes_per_lead,
            "effort_reduction_pct": None,
            "effort_target_20pct_met": False,
            "due_on_time_rate": None,
            "due_date_non_inferior": None,
            "total_overrides": 0,
        }

    unique_dates = [date.fromisoformat(str(d)[:10]) for d in df["business_date"].dropna().unique()]
    observed_bd = sum(1 for d in unique_dates if is_vn_business_day(d))

    total_enq = int(df["enquiries_reviewed"].sum())
    total_effort = float((df["queue_prep_minutes"] + df["triage_minutes"]).sum())
    avg_min = round(total_effort / total_enq, 3) if total_enq > 0 else None

    if avg_min is not None and baseline_minutes_per_lead > 0:
        reduction_pct = round((baseline_minutes_per_lead - avg_min) / baseline_minutes_per_lead * 100.0, 2)
    else:
        reduction_pct = None

    total_due = int(df["due_total"].sum())
    total_on_time = int(df["due_on_time"].sum())
    on_time_rate = round(total_on_time / total_due, 4) if total_due > 0 else None

    if baseline_on_time_rate is None or on_time_rate is None:
        due_non_inferior = None
    else:
        due_non_inferior = on_time_rate >= baseline_on_time_rate

    return {
        "observed_business_days": observed_bd,
        "min_business_days_required": MIN_PILOT_BUSINESS_DAYS,
        "min_20bd_met": observed_bd >= MIN_PILOT_BUSINESS_DAYS,
        "total_enquiries_reviewed": total_enq,
        "avg_minutes_per_lead": avg_min,
        "baseline_minutes_per_lead": baseline_minutes_per_lead,
        "effort_reduction_pct": reduction_pct,
        "effort_target_20pct_met": (reduction_pct is not None) and (reduction_pct >= TARGET_EFFORT_REDUCTION_PCT),
        "due_on_time_rate": on_time_rate,
        "due_date_non_inferior": due_non_inferior,
        "total_overrides": int(df["override_count"].sum()),
    }

