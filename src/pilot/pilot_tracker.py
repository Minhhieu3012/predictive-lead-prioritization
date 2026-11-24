"""PLPT-57 & PLPT-68: Pilot Assignment, Effort Telemetry & 20-Business-Day Summary."""

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


def _open_pilot_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _assignment_key(
    account_key: str | None = None,
    project_ref_code: str | None = None,
) -> tuple[str, str]:
    acct = str(account_key or "").strip().upper()
    proj = str(project_ref_code or "").strip().upper()
    if proj:
        return ("PROJECT", proj)
    if acct:
        return ("ACCOUNT", acct)
    raise ValueError("Cần cung cấp account_key hoặc project_ref_code khi phân bổ thí điểm.")


def assign_account_treatment(
    account_key: str,
    *,
    salt: str = "songnhat-pilot-v1",
    control_ratio: float = 0.5,
    project_ref_code: str | None = None,
) -> str:
    """
    RFP Mục 10 & WP 05: Phân bổ nhóm thí điểm ở cấp độ tài khoản (`account_key`)
    hoặc mã dự án (`project_ref_code`) bằng hàm băm tất định.
    """
    scope_type, scope_val = _assignment_key(
        account_key=account_key,
        project_ref_code=project_ref_code,
    )
    if not (0.0 <= control_ratio <= 1.0):
        raise ValueError("control_ratio phải nằm trong [0.0, 1.0].")

    digest = hashlib.sha256(f"{salt}:{scope_type}:{scope_val}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "RULES_CONTROL" if bucket < control_ratio else "SUPERVISED_TREATMENT"


def assign_cohort_by_account(
    enquiries_df: pd.DataFrame,
    *,
    salt: str = "songnhat-pilot-v1",
    control_ratio: float = 0.5,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Gắn nhãn `treatment_arm` nhất quán theo `account_key` (hoặc `project_ref_code`) và xác minh không xung đột."""
    out = enquiries_df.copy()
    if out.empty:
        out["treatment_arm"] = pd.Series(dtype=str)
        return out, {
            "total_rows": 0,
            "unique_accounts": 0,
            "conflicting_accounts": 0,
            "account_assignment_conflict_count": 0,
            "arm_counts": {},
        }

    has_proj = "project_ref_code" in out.columns

    def _row_arm(row: pd.Series) -> str:
        proj = (
            str(row["project_ref_code"])
            if has_proj and pd.notna(row["project_ref_code"])
            else None
        )
        acct = (
            str(row["account_key"])
            if "account_key" in out.columns and pd.notna(row["account_key"])
            else ""
        )
        return assign_account_treatment(
            acct or (proj or ""),
            salt=salt,
            control_ratio=control_ratio,
            project_ref_code=proj if proj else None,
        )

    out["treatment_arm"] = out.apply(_row_arm, axis=1)

    group_col = (
        "project_ref_code"
        if has_proj and out["project_ref_code"].notna().any()
        else "account_key"
    )
    per_group_arms = out.groupby(group_col)["treatment_arm"].nunique()
    conflicting = int((per_group_arms > 1).sum())
    arm_counts = {
        str(k): int(v) for k, v in out["treatment_arm"].value_counts().to_dict().items()
    }

    return out, {
        "total_rows": int(len(out)),
        "unique_accounts": int(out[group_col].nunique()),
        "conflicting_accounts": conflicting,
        "account_assignment_conflict_count": conflicting,
        "arm_counts": arm_counts,
    }


def ensure_pilot_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pilot_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_scope TEXT NOT NULL,
            assignment_key TEXT NOT NULL UNIQUE,
            account_key TEXT,
            project_ref_code TEXT,
            treatment_arm TEXT NOT NULL,
            assigned_at TEXT NOT NULL
        )
        """
    )
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
            triage_duration_seconds REAL NOT NULL DEFAULT 0.0,
            stewardship_resolution_minutes REAL NOT NULL DEFAULT 0.0,
            batch_failure_recovery_minutes REAL NOT NULL DEFAULT 0.0,
            due_total INTEGER NOT NULL DEFAULT 0,
            due_on_time INTEGER NOT NULL DEFAULT 0,
            override_count INTEGER NOT NULL DEFAULT 0,
            logged_at TEXT NOT NULL
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pilot_effort_logs)").fetchall()}
    if "triage_duration_seconds" not in cols:
        conn.execute(
            "ALTER TABLE pilot_effort_logs ADD COLUMN triage_duration_seconds REAL NOT NULL DEFAULT 0.0"
        )
    if "stewardship_resolution_minutes" not in cols:
        conn.execute(
            "ALTER TABLE pilot_effort_logs ADD COLUMN stewardship_resolution_minutes REAL NOT NULL DEFAULT 0.0"
        )
    if "batch_failure_recovery_minutes" not in cols:
        conn.execute(
            "ALTER TABLE pilot_effort_logs ADD COLUMN batch_failure_recovery_minutes REAL NOT NULL DEFAULT 0.0"
        )


def upsert_pilot_assignment(
    db_path: str | Path,
    *,
    account_key: str | None = None,
    project_ref_code: str | None = None,
    salt: str = "songnhat-pilot-v1",
    control_ratio: float = 0.5,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Lưu hoặc trả về phân bổ nhất quán theo account_key / project_ref_code trong pilot_assignments."""
    scope_type, scope_val = _assignment_key(
        account_key=account_key,
        project_ref_code=project_ref_code,
    )
    arm = assign_account_treatment(
        account_key or scope_val,
        salt=salt,
        control_ratio=control_ratio,
        project_ref_code=project_ref_code,
    )
    assigned_at = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()
    acct_norm = str(account_key or "").strip().upper() or None
    proj_norm = str(project_ref_code or "").strip().upper() or None

    with contextlib.closing(_open_pilot_db(db_path)) as conn:
        with conn:
            ensure_pilot_tables(conn)
            existing = conn.execute(
                "SELECT treatment_arm, assigned_at FROM pilot_assignments WHERE assignment_key = ?",
                (scope_val,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO pilot_assignments (
                        assignment_scope, assignment_key, account_key, project_ref_code,
                        treatment_arm, assigned_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (scope_type, scope_val, acct_norm, proj_norm, arm, assigned_at),
                )
                effective_arm = arm
                effective_at = assigned_at
            else:
                effective_arm = str(existing[0])
                effective_at = str(existing[1])

    return {
        "assignment_scope": scope_type,
        "assignment_key": scope_val,
        "account_key": acct_norm,
        "project_ref_code": proj_norm,
        "treatment_arm": effective_arm,
        "assigned_at": effective_at,
    }


def count_assignment_conflicts(conn: sqlite3.Connection) -> int:
    """Đếm số account_key hoặc project_ref_code bị gán nhiều hơn 1 treatment_arm."""
    ensure_pilot_tables(conn)
    acct_conflicts = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT account_key
            FROM pilot_assignments
            WHERE account_key IS NOT NULL AND TRIM(account_key) != ''
            GROUP BY account_key
            HAVING COUNT(DISTINCT treatment_arm) > 1
        )
        """
    ).fetchone()[0]
    proj_conflicts = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT project_ref_code
            FROM pilot_assignments
            WHERE project_ref_code IS NOT NULL AND TRIM(project_ref_code) != ''
            GROUP BY project_ref_code
            HAVING COUNT(DISTINCT treatment_arm) > 1
        )
        """
    ).fetchone()[0]
    return int((acct_conflicts or 0) + (proj_conflicts or 0))


def log_pilot_effort_session(
    db_path: str | Path,
    *,
    business_date: str | date,
    staff_id: str,
    enquiries_reviewed: int,
    queue_prep_minutes: float,
    triage_minutes: float | None = None,
    triage_duration_seconds: float | None = None,
    stewardship_resolution_minutes: float = 0.0,
    batch_failure_recovery_minutes: float = 0.0,
    due_total: int = 0,
    due_on_time: int = 0,
    override_count: int = 0,
    treatment_arm: str = "RULES_CONTROL",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ghi nhận nhật ký công sức chuẩn bị hàng đợi & phân loại hàng ngày trong đợt thí điểm 20 ngày làm việc."""
    b_date = (
        business_date
        if isinstance(business_date, date)
        else date.fromisoformat(str(business_date)[:10])
    )
    if triage_minutes is None and triage_duration_seconds is not None:
        eff_triage_min = float(triage_duration_seconds) / 60.0
    else:
        eff_triage_min = float(triage_minutes or 0.0)

    eff_triage_sec = (
        float(triage_duration_seconds)
        if triage_duration_seconds is not None
        else round(eff_triage_min * 60.0, 2)
    )
    eff_steward_min = float(stewardship_resolution_minutes or 0.0)
    eff_recovery_min = float(batch_failure_recovery_minutes or 0.0)

    if (
        enquiries_reviewed < 0
        or queue_prep_minutes < 0
        or eff_triage_min < 0
        or eff_triage_sec < 0
        or eff_steward_min < 0
        or eff_recovery_min < 0
    ):
        raise ValueError("Số lượng hồ sơ và thời gian xử lý phải >= 0.")
    if due_on_time > due_total:
        raise ValueError("due_on_time không được lớn hơn due_total.")

    logged_at = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0).isoformat()
    with contextlib.closing(_open_pilot_db(db_path)) as conn:
        with conn:
            ensure_pilot_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO pilot_effort_logs (
                    business_date, staff_id, treatment_arm, enquiries_reviewed,
                    queue_prep_minutes, triage_minutes, triage_duration_seconds,
                    stewardship_resolution_minutes, batch_failure_recovery_minutes,
                    due_total, due_on_time, override_count, logged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b_date.isoformat(),
                    str(staff_id or "sales_staff").strip(),
                    str(treatment_arm or "RULES_CONTROL").strip().upper(),
                    int(enquiries_reviewed),
                    float(queue_prep_minutes),
                    eff_triage_min,
                    eff_triage_sec,
                    eff_steward_min,
                    eff_recovery_min,
                    int(due_total),
                    int(due_on_time),
                    int(override_count),
                    logged_at,
                ),
            )
            row_id = int(cur.lastrowid)

    total_effort_min = round(
        float(queue_prep_minutes) + eff_triage_min + eff_steward_min + eff_recovery_min,
        2,
    )
    return {
        "id": row_id,
        "business_date": b_date.isoformat(),
        "is_vn_business_day": is_vn_business_day(b_date),
        "staff_id": str(staff_id or "sales_staff").strip(),
        "enquiries_reviewed": int(enquiries_reviewed),
        "triage_duration_seconds": round(eff_triage_sec, 2),
        "stewardship_resolution_minutes": round(eff_steward_min, 2),
        "batch_failure_recovery_minutes": round(eff_recovery_min, 2),
        "total_effort_minutes": total_effort_min,
        "logged_at": logged_at,
    }


def summarize_pilot_20bd(
    db_path: str | Path,
    *,
    baseline_minutes_per_lead: float = DEFAULT_BASELINE_TRIAGE_MINUTES,
    baseline_on_time_rate: float | None = None,
) -> dict[str, Any]:
    """
    RFP Mục 8 & Spec 10: Tổng hợp giai đoạn thí điểm >= 20 ngày làm việc VN,
    đối chiếu mục tiêu giảm >= 20% công sức và tỷ lệ đúng hạn SLA.
    """
    with contextlib.closing(_open_pilot_db(db_path)) as conn:
        with conn:
            ensure_pilot_tables(conn)
            df = pd.read_sql_query("SELECT * FROM pilot_effort_logs", conn)
            conflict_count = count_assignment_conflicts(conn)

    if df.empty:
        return {
            "observed_business_days": 0,
            "business_days_elapsed": 0,
            "min_business_days_required": MIN_PILOT_BUSINESS_DAYS,
            "min_20bd_met": False,
            "total_enquiries_reviewed": 0,
            "avg_minutes_per_lead": None,
            "pilot_minutes_per_enquiry": None,
            "baseline_minutes_per_lead": baseline_minutes_per_lead,
            "baseline_minutes_per_enquiry": baseline_minutes_per_lead,
            "effort_reduction_pct": None,
            "effort_target_20pct_met": False,
            "due_on_time_rate": None,
            "on_time_sla_rate_pct": None,
            "due_date_non_inferior": None,
            "total_overrides": 0,
            "account_assignment_conflict_count": conflict_count,
        }

    unique_dates = [
        date.fromisoformat(str(d)[:10]) for d in df["business_date"].dropna().unique()
    ]
    observed_bd = sum(1 for d in unique_dates if is_vn_business_day(d))

    total_enq = int(df["enquiries_reviewed"].sum())
    extra_cols = [
        c
        for c in ("stewardship_resolution_minutes", "batch_failure_recovery_minutes")
        if c in df.columns
    ]
    extra_sum = float(df[extra_cols].fillna(0.0).sum().sum()) if extra_cols else 0.0
    total_effort = float((df["queue_prep_minutes"] + df["triage_minutes"]).sum()) + extra_sum
    avg_min = round(total_effort / total_enq, 3) if total_enq > 0 else None

    if avg_min is not None and baseline_minutes_per_lead > 0:
        reduction_pct = round(
            (baseline_minutes_per_lead - avg_min) / baseline_minutes_per_lead * 100.0,
            2,
        )
    else:
        reduction_pct = None

    total_due = int(df["due_total"].sum())
    total_on_time = int(df["due_on_time"].sum())
    on_time_rate = round(total_on_time / total_due, 4) if total_due > 0 else None
    on_time_sla_rate_pct = round(on_time_rate * 100.0, 2) if on_time_rate is not None else None

    if baseline_on_time_rate is None or on_time_rate is None:
        due_non_inferior = None
    else:
        due_non_inferior = on_time_rate >= baseline_on_time_rate

    return {
        "observed_business_days": observed_bd,
        "business_days_elapsed": observed_bd,
        "min_business_days_required": MIN_PILOT_BUSINESS_DAYS,
        "min_20bd_met": observed_bd >= MIN_PILOT_BUSINESS_DAYS,
        "total_enquiries_reviewed": total_enq,
        "avg_minutes_per_lead": avg_min,
        "pilot_minutes_per_enquiry": avg_min,
        "baseline_minutes_per_lead": baseline_minutes_per_lead,
        "baseline_minutes_per_enquiry": baseline_minutes_per_lead,
        "effort_reduction_pct": reduction_pct,
        "effort_target_20pct_met": (reduction_pct is not None)
        and (reduction_pct >= TARGET_EFFORT_REDUCTION_PCT),
        "due_on_time_rate": on_time_rate,
        "on_time_sla_rate_pct": on_time_sla_rate_pct,
        "due_date_non_inferior": due_non_inferior,
        "total_overrides": int(df["override_count"].sum()),
        "account_assignment_conflict_count": conflict_count,
    }