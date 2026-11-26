"""PLPT-27 / PLPT-46: Deterministic Synthetic Fixture Factory for 40 Control Fixtures (CF-01 to CF-40)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import contextlib
import sqlite3
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

ALL_CONTROL_FIXTURE_IDS: list[str] = [f"CF-{i:02d}" for i in range(1, 41)]


def make_cf_row(**overrides: Any) -> dict[str, Any]:
    """Tạo một bản ghi enquiry giả lập tất định phục vụ kiểm thử CF-01..CF-40."""
    now = datetime.now(VN_TZ)
    default_t0 = (now - timedelta(hours=5)).replace(microsecond=0).isoformat()
    row: dict[str, Any] = {
        "enquiry_id": "CF-ENQ-001",
        "account_key": "CF-ACCT-001",
        "cohort": "2026-11",
        "source": "WEB_FORM",
        "t0_time": default_t0,
        "extraction_time": (now - timedelta(hours=1)).replace(microsecond=0).isoformat(),
        "product_family": "Motor_DongCo",
        "project_ref_code": "PRJ-01",
        "spec_completeness": "Full",
        "is_urgent_replacement": "Normal",
        "contract_due_date": (now + timedelta(days=20)).date().isoformat(),
        "workflow_state": "New",
        "industry_at_t0": "Manufacturing",
        "enquiry_channel_at_t0": "WEB_FORM",
        "region_at_t0": "South",
        "account_age_days_at_t0": 180,
        "prior_purchase_count_at_t0": 2,
        "estimated_value_band_at_t0": "MEDIUM",
        "feature_event_time": default_t0,
        "feature_available_at": default_t0,
    }
    row.update(overrides)
    if "t0_time" in overrides:
        if "feature_event_time" not in overrides:
            row["feature_event_time"] = overrides["t0_time"]
        if "feature_available_at" not in overrides:
            row["feature_available_at"] = overrides["t0_time"]
    return row


def create_clean_db(db_path: Path, rows: list[dict[str, Any]]) -> None:
    """Khởi tạo SQLite clean_enquiries cô lập từ danh sách bản ghi fixture."""
    df = pd.DataFrame(rows)
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            df.to_sql("clean_enquiries", conn, index=False, if_exists="replace")

