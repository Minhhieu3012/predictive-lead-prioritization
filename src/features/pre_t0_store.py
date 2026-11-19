"""PLPT-26: Pre-t0 Feature Store & Anti-Leakage Controls."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

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
    forbidden = {c for c in columns if c in {"label", "event_type", "qualification_evidence", "maturity_state"} or c.endswith("_DO_NOT_USE")}
    if forbidden:
        raise LeakageError(f"Architectural guard: outcome/forbidden columns cannot enter scoring predictors: {sorted(forbidden)}")


def validate_feature_columns(feature_cols: list[str]) -> None:
    if len(feature_cols) > MAX_FEATURE_COLUMNS:
        raise ValueError(f"Feature column count {len(feature_cols)} exceeds cap of {MAX_FEATURE_COLUMNS}.")
    bad = [c for c in feature_cols if c in PROHIBITED_FEATURE_COLUMNS or c.endswith("_DO_NOT_USE")]
    if bad:
        raise LeakageError(f"Prohibited PII/identifier/post-t0 columns detected in predictors: {bad}")


def build_pre_t0_features(
    enquiries_df: pd.DataFrame,
    *,
    strict_leakage: bool = True,
    drop_post_t0_rows: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Xây dựng ma trận đặc trưng tại thời điểm t0:
    1. Kiểm tra feature_available_at <= t0_time và feature_event_time <= t0_time (CF-01, CF-02).
    2. Chặn cột *_DO_NOT_USE, PII, account_key, enquiry_id khỏi danh sách feature_cols (CF-03, CF-34).
    3. Tính pre_t0_account_enquiry_count chỉ từ các bản ghi có t0_time < current_t0_time (CF-04).
    4. Bảo đảm số lượng cột đặc trưng <= 30.
    """
    if enquiries_df.empty:
        return pd.DataFrame(), []

    df = enquiries_df.copy()

    leak_cols = [
        c for c in df.columns
        if c.endswith("_DO_NOT_USE") or c in {"quotation_value_post_t0", "order_outcome_post_t0"}
    ]
    if leak_cols:
        if strict_leakage:
            raise LeakageError(f"Post-t0 leakage columns detected in dataset: {leak_cols}")
        df = df.drop(columns=leak_cols)

    valid_mask = []
    parsed_t0_list: list[datetime | None] = []
    for _, row in df.iterrows():
        t0_dt = _parse_iso(row.get("t0_time"))
        parsed_t0_list.append(t0_dt)
        avail_dt = _parse_iso(row.get("feature_available_at"))
        event_dt = _parse_iso(row.get("feature_event_time"))

        is_leak = False
        if t0_dt is not None:
            if avail_dt is not None and avail_dt > t0_dt:
                is_leak = True
            if event_dt is not None and event_dt > t0_dt:
                is_leak = True
        if is_leak and strict_leakage and not drop_post_t0_rows:
            raise LeakageError(
                f"Post-t0 feature leakage on enquiry_id={row.get('enquiry_id')}: "
                f"feature_available_at={row.get('feature_available_at')} > t0_time={row.get('t0_time')}"
            )
        valid_mask.append(not is_leak)

    if drop_post_t0_rows:
        df = df.loc[valid_mask].copy()
        parsed_t0_list = [dt for dt, ok in zip(parsed_t0_list, valid_mask) if ok]

    df["_parsed_t0"] = parsed_t0_list

    acct_series = (
        df["account_key"].fillna("").astype(str)
        if "account_key" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )
    pre_counts: list[int] = []
    acct_times: dict[str, list[datetime]] = {}
    for acct, dt in zip(acct_series, parsed_t0_list):
        if acct and dt is not None:
            acct_times.setdefault(acct, []).append(dt)
    for acct, dt in zip(acct_series, parsed_t0_list):
        if not acct or dt is None:
            pre_counts.append(0)
        else:
            pre_counts.append(sum(1 for other_dt in acct_times.get(acct, []) if other_dt < dt))
    df["pre_t0_account_enquiry_count"] = pre_counts

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

    feature_cols = [c for c in APPROVED_FEATURE_DICTIONARY.keys() if c in df.columns]
    validate_feature_columns(feature_cols)

    meta_cols = [c for c in ("enquiry_id", "t0_time", "account_key", "feature_event_time") if c in df.columns]
    out_df = df[meta_cols + feature_cols].copy()
    return out_df, feature_cols
