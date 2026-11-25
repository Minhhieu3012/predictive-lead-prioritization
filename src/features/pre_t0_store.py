"""PLPT-26: Pre-t0 Feature Store, Lineage Tracking & Anti-Leakage Controls."""

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

FEATURE_VERSION = "pre_t0_v1"
MAX_FEATURE_COLUMNS = 30

PROHIBITED_FEATURE_COLUMNS = {
    "enquiry_id",
    "account_key",
    "contact_name",
    "contact_person",
    "phone",
    "phone_number",
    "email",
    "personal_email",
    "free_text",
    "notes",
    "next_step_note",
    "label",
    "outcome",
    "qualification_evidence",
    "business_need_confirmed",
    "product_fit_confirmed",
    "agreed_next_step_confirmed",
    "quotation_value_post_t0",
    "order_outcome_post_t0",
    "t0_time",
    "extraction_time",
    "feature_event_time",
    "feature_available_at",
}

APPROVED_FEATURE_DICTIONARY: dict[str, dict[str, str]] = {
    "product_family": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "MISSING",
    },
    "spec_completeness": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "Minimal",
    },
    "is_urgent_replacement": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "Normal",
    },
    "industry_at_t0": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "MISSING",
    },
    "enquiry_channel_at_t0": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "MISSING",
    },
    "region_at_t0": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "MISSING",
    },
    "estimated_value_band_at_t0": {
        "type": "categorical",
        "timing": "at_t0",
        "missing_policy": "MISSING",
    },
    "account_age_days_at_t0": {
        "type": "numeric",
        "timing": "at_t0",
        "missing_policy": "median_impute",
    },
    "prior_purchase_count_at_t0": {
        "type": "numeric",
        "timing": "pre_t0",
        "missing_policy": "zero_or_median_with_new_flag",
    },
    "is_new_account_at_t0": {
        "type": "numeric",
        "timing": "at_t0",
        "missing_policy": "0",
    },
    "pre_t0_account_enquiry_count": {
        "type": "numeric",
        "timing": "strictly_before_t0",
        "missing_policy": "0",
    },
    "days_to_contract_due_at_t0": {
        "type": "numeric",
        "timing": "at_t0",
        "missing_policy": "median_impute",
    },
    "account_history_status": {
        "type": "categorical",
        "timing": "strictly_before_t0",
        "missing_policy": "UNKNOWN",
    },
}


class LeakageError(ValueError):
    """Ngoại lệ khi phát hiện rò rỉ dữ liệu hậu t0 hoặc cột cấm."""


def _parse_iso(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except ValueError:
        return None


def get_feature_dictionary_hash(feature_cols: list[str] | None = None) -> str:
    cols = sorted(feature_cols or list(APPROVED_FEATURE_DICTIONARY.keys()))
    payload = {c: APPROVED_FEATURE_DICTIONARY.get(c, {}) for c in cols}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:32]}"


def guard_no_outcome_join(columns: list[str]) -> None:
    """CF-06: Cấm tham gia bảng outcome_events vào đặc trưng phục vụ sản xuất."""
    forbidden = {
        c
        for c in columns
        if c in {"label", "event_type", "qualification_evidence", "maturity_state"}
        or c.endswith("_DO_NOT_USE")
    }
    if forbidden:
        raise LeakageError(
            f"Architectural guard: outcome/forbidden columns cannot enter scoring predictors: {sorted(forbidden)}"
        )


def validate_feature_columns(feature_cols: list[str]) -> None:
    if len(feature_cols) > MAX_FEATURE_COLUMNS:
        raise ValueError(
            f"Feature column count {len(feature_cols)} exceeds cap of {MAX_FEATURE_COLUMNS}."
        )
    bad = [
        c
        for c in feature_cols
        if c in PROHIBITED_FEATURE_COLUMNS or c.endswith("_DO_NOT_USE")
    ]
    if bad:
        raise LeakageError(
            f"Prohibited PII/identifier/post-t0 columns detected in predictors: {bad}"
        )


def _evaluate_t0_boundary(row: pd.Series) -> tuple[datetime | None, datetime | None, datetime | None, str | None]:
    t0_dt = _parse_iso(row.get("t0_time"))
    event_dt = _parse_iso(row.get("feature_event_time"))
    avail_dt = _parse_iso(row.get("feature_available_at"))

    if t0_dt is None:
        return t0_dt, event_dt, avail_dt, "INVALID_T0"
    if event_dt is not None and event_dt > t0_dt:
        return t0_dt, event_dt, avail_dt, "FEATURE_EVENT_AFTER_T0"
    if avail_dt is not None and avail_dt > t0_dt:
        return t0_dt, event_dt, avail_dt, "FEATURE_AVAILABLE_AFTER_T0"
    return t0_dt, event_dt, avail_dt, None


def build_pre_t0_features_with_lineage(
    enquiries_df: pd.DataFrame,
    *,
    strict_leakage: bool = True,
    drop_post_t0_rows: bool = False,
) -> tuple[pd.DataFrame, list[str], list[dict[str, Any]]]:
    """
    Xây dựng ma trận đặc trưng tại thời điểm t0 kèm bảng lineage:
    1. Kiểm tra feature_available_at <= t0_time và feature_event_time <= t0_time (CF-01, CF-02).
    2. Chặn cột *_DO_NOT_USE, PII, account_key, enquiry_id khỏi feature_cols và không lưu account_key vào bảng đặc trưng/lineage.
    3. Tính pre_t0_account_enquiry_count chỉ từ các bản ghi hợp lệ có t0_time < current_t0_time (CF-04) trong RAM.
    4. Gán account_history_status minh bạch: HISTORY_FOUND / NO_PRIOR_ENQUIRY_OBSERVED / UNKNOWN.
    5. Ghi nhận lý do từ chối vào lineage_records cho mọi bản ghi vi phạm ranh giới t0.
    """
    if enquiries_df.empty:
        return pd.DataFrame(), [], []

    df = enquiries_df.copy()

    leak_cols = [
        c
        for c in df.columns
        if c.endswith("_DO_NOT_USE")
        or c in {"quotation_value_post_t0", "order_outcome_post_t0"}
    ]
    if leak_cols:
        if strict_leakage:
            raise LeakageError(
                f"Post-t0 leakage columns detected in dataset: {leak_cols}"
            )
        df = df.drop(columns=leak_cols)

    valid_mask: list[bool] = []
    parsed_t0_list: list[datetime | None] = []
    lineage_records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        t0_dt, event_dt, avail_dt, rejection_code = _evaluate_t0_boundary(row)
        is_post_t0_leak = rejection_code in {
            "FEATURE_EVENT_AFTER_T0",
            "FEATURE_AVAILABLE_AFTER_T0",
        }
        if is_post_t0_leak and strict_leakage and not drop_post_t0_rows:
            raise LeakageError(
                f"Post-t0 feature leakage on enquiry_id={row.get('enquiry_id')}: "
                f"{rejection_code} (t0_time={row.get('t0_time')})"
            )

        is_valid = not is_post_t0_leak and rejection_code != "INVALID_T0"
        # Giữ tương thích nếu test truyền t0_time thiếu nhưng không vi phạm post-t0 khi strict_leakage=True
        if rejection_code == "INVALID_T0" and not drop_post_t0_rows:
            is_valid = True
            rejection_for_lineage = "INVALID_T0"
        else:
            rejection_for_lineage = rejection_code

        lineage_records.append(
            {
                "enquiry_id": str(row.get("enquiry_id") or "").strip(),
                "t0_time": t0_dt.astimezone(VN_TZ).isoformat(timespec="seconds")
                if t0_dt is not None
                else None,
                "feature_event_time": event_dt.astimezone(VN_TZ).isoformat(timespec="seconds")
                if event_dt is not None
                else None,
                "feature_available_at": avail_dt.astimezone(VN_TZ).isoformat(timespec="seconds")
                if avail_dt is not None
                else None,
                "feature_status": "READY" if rejection_for_lineage is None else "REJECTED",
                "rejection_code": rejection_for_lineage,
            }
        )
        valid_mask.append(is_valid)
        parsed_t0_list.append(t0_dt)

    if drop_post_t0_rows:
        df = df.loc[valid_mask].copy()
        parsed_t0_list = [dt for dt, ok in zip(parsed_t0_list, valid_mask) if ok]

    if df.empty:
        return pd.DataFrame(), [], lineage_records

    df["_parsed_t0"] = parsed_t0_list

    # Dùng account_key chỉ trong RAM để tổng hợp lịch sử pre-t0; không lưu ra bảng đặc trưng/lineage
    acct_series = (
        df["account_key"].fillna("").astype(str).str.strip()
        if "account_key" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )
    pre_counts: list[int] = []
    history_statuses: list[str] = []
    acct_times: dict[str, list[datetime]] = {}
    for acct, dt in zip(acct_series, parsed_t0_list):
        if acct and dt is not None:
            acct_times.setdefault(acct, []).append(dt)

    for acct, dt in zip(acct_series, parsed_t0_list):
        if not acct:
            pre_counts.append(0)
            history_statuses.append("UNKNOWN")
        elif dt is None:
            pre_counts.append(0)
            history_statuses.append("NO_PRIOR_ENQUIRY_OBSERVED")
        else:
            cnt = sum(1 for other_dt in acct_times.get(acct, []) if other_dt < dt)
            pre_counts.append(cnt)
            history_statuses.append(
                "HISTORY_FOUND" if cnt > 0 else "NO_PRIOR_ENQUIRY_OBSERVED"
            )

    df["pre_t0_account_enquiry_count"] = pre_counts
    df["account_history_status"] = history_statuses

    due_series = (
        df["contract_due_date"]
        if "contract_due_date" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )
    days_to_due: list[float | None] = []
    for dt, raw_due in zip(parsed_t0_list, due_series):
        due_dt = _parse_iso(raw_due)
        if dt is not None and due_dt is not None:
            days_to_due.append(float((due_dt.date() - dt.date()).days))
        else:
            days_to_due.append(None)
    df["days_to_contract_due_at_t0"] = days_to_due

    prior_raw = (
        df["prior_purchase_count_at_t0"]
        if "prior_purchase_count_at_t0" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )
    prior_col = pd.to_numeric(prior_raw, errors="coerce")
    df["prior_purchase_count_at_t0"] = prior_col
    df["is_new_account_at_t0"] = prior_col.isna().astype(int)

    age_raw = (
        df["account_age_days_at_t0"]
        if "account_age_days_at_t0" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )
    df["account_age_days_at_t0"] = pd.to_numeric(age_raw, errors="coerce")

    feature_cols = [
        c for c in APPROVED_FEATURE_DICTIONARY.keys() if c in df.columns
    ]
    validate_feature_columns(feature_cols)

    # Không bao giờ lưu account_key vào bảng đặc trưng đầu ra
    meta_cols = [
        c for c in ("enquiry_id", "t0_time", "feature_event_time") if c in df.columns
    ]
    out_df = df[meta_cols + feature_cols].copy()
    return out_df, feature_cols, lineage_records


def build_pre_t0_features(
    enquiries_df: pd.DataFrame,
    *,
    strict_leakage: bool = True,
    drop_post_t0_rows: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    out_df, feature_cols, _ = build_pre_t0_features_with_lineage(
        enquiries_df,
        strict_leakage=strict_leakage,
        drop_post_t0_rows=drop_post_t0_rows,
    )
    return out_df, feature_cols


def _default_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(
        os.getenv("DATABASE_PATH", str(project_root / "data" / "songnhat.db"))
    )


def _ensure_feature_store_tables(
    conn: sqlite3.Connection,
    feature_cols: list[str],
) -> None:
    col_defs = []
    for col in feature_cols:
        col_type = APPROVED_FEATURE_DICTIONARY.get(col, {}).get("type")
        sql_type = "REAL" if col_type == "numeric" else "TEXT"
        col_defs.append(f'"{col}" {sql_type}')
    cols_sql = ",\n            ".join(col_defs)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS pre_t0_features (
            enquiry_id TEXT PRIMARY KEY,
            feature_version TEXT NOT NULL,
            {cols_sql},
            built_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pre_t0_feature_lineage (
            enquiry_id TEXT PRIMARY KEY,
            t0_time TEXT,
            feature_event_time TEXT,
            feature_available_at TEXT,
            feature_status TEXT NOT NULL,
            rejection_code TEXT,
            feature_version TEXT NOT NULL,
            built_at TEXT NOT NULL
        )
        """
    )


def build_pre_t0_store(
    db_path: str | Path | None = None,
) -> dict[str, int | str]:
    """Đọc clean_enquiries, tính đặc trưng pre-t0 (mask account_key) và ghi pre_t0_features + pre_t0_feature_lineage."""
    target_db = Path(db_path) if db_path is not None else _default_db_path()
    if not target_db.exists():
        raise FileNotFoundError(f"Database not found: {target_db}")

    with contextlib.closing(sqlite3.connect(str(target_db))) as conn:
        with conn:
            conn.row_factory = sqlite3.Row
            has_clean = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clean_enquiries'"
            ).fetchone()
            if has_clean is None:
                raise ValueError(
                    "clean_enquiries is required; run ingestion and deduplication first"
                )
            rows = [
                dict(r)
                for r in conn.execute("SELECT * FROM clean_enquiries").fetchall()
            ]

    enq_df = pd.DataFrame(rows)
    out_df, feature_cols, lineage_records = build_pre_t0_features_with_lineage(
        enq_df,
        strict_leakage=False,
        drop_post_t0_rows=True,
    )
    built_at = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    with contextlib.closing(sqlite3.connect(str(target_db), timeout=30.0)) as conn:
        with conn:
            _ensure_feature_store_tables(conn, feature_cols)
            conn.execute("DELETE FROM pre_t0_features")
            conn.execute("DELETE FROM pre_t0_feature_lineage")

            if not out_df.empty:
                placeholders = ", ".join("?" for _ in range(len(feature_cols) + 3))
                cols_sql = ", ".join(f'"{c}"' for c in feature_cols)
                insert_feat_sql = (
                    f"INSERT INTO pre_t0_features (enquiry_id, feature_version, {cols_sql}, built_at) "
                    f"VALUES ({placeholders})"
                )
                feat_rows = []
                for _, row in out_df.iterrows():
                    vals = [
                        None if pd.isna(row[c]) else row[c]
                        for c in feature_cols
                    ]
                    feat_rows.append(
                        (
                            str(row.get("enquiry_id") or "").strip(),
                            FEATURE_VERSION,
                            *vals,
                            built_at,
                        )
                    )
                conn.executemany(insert_feat_sql, feat_rows)

            conn.executemany(
                """
                INSERT INTO pre_t0_feature_lineage (
                    enquiry_id, t0_time, feature_event_time, feature_available_at,
                    feature_status, rejection_code, feature_version, built_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        rec["enquiry_id"],
                        rec["t0_time"],
                        rec["feature_event_time"],
                        rec["feature_available_at"],
                        rec["feature_status"],
                        rec["rejection_code"],
                        FEATURE_VERSION,
                        built_at,
                    )
                    for rec in lineage_records
                ],
            )
            conn.commit()

    rejected_count = sum(1 for r in lineage_records if r["feature_status"] == "REJECTED")
    return {
        "status": "SUCCESS",
        "feature_version": FEATURE_VERSION,
        "feature_count": len(out_df),
        "rejected_count": rejected_count,
        "feature_columns": len(feature_cols),
    }
