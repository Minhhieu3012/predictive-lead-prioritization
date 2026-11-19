"""Tests cho CF-09: Uncertain duplicate routing to steward_review_queue (DATA_STEWARD)."""

from pathlib import Path
import sqlite3

import pandas as pd

from src.ingest.deduplicator import deduplicate_dataframe_with_steward, deduplicate_enquiries


def _base_row(**overrides):
    row = {
        "enquiry_id": "SN-001",
        "account_key": "ACCT-100",
        "cohort": "train",
        "source": "WEB_FORM",
        "t0_time": "2026-10-01T08:00:00+07:00",
        "extraction_time": "2026-10-01T09:00:00+07:00",
        "product_family": "Motor_DongCo",
        "project_ref_code": "PRJ-A",
        "spec_completeness": "Full",
        "is_urgent_replacement": "Normal",
        "contract_due_date": "2026-10-30",
        "workflow_state": "New",
        "industry_at_t0": "Manufacturing",
        "enquiry_channel_at_t0": "WEB_FORM",
        "region_at_t0": "South",
        "account_age_days_at_t0": 100,
        "prior_purchase_count_at_t0": 2,
        "estimated_value_band_at_t0": "MEDIUM",
        "feature_event_time": "2026-10-01T08:00:00+07:00",
        "feature_available_at": "2026-10-01T08:00:00+07:00",
    }
    row.update(overrides)
    return row


def test_cf09_different_project_ref_kept_independent():
    df = pd.DataFrame([
        _base_row(enquiry_id="SN-1", project_ref_code="PRJ-1"),
        _base_row(enquiry_id="SN-2", project_ref_code="PRJ-2", enquiry_channel_at_t0="DIRECT_EMAIL"),
    ])
    clean_df, merge_df, steward_df = deduplicate_dataframe_with_steward(df)
    assert len(clean_df) == 2
    assert len(merge_df) == 0
    assert len(steward_df) == 0


def test_cf09_missing_project_ref_on_one_side_routes_to_steward(tmp_path: Path):
    db_path = tmp_path / "cf09_missing.db"
    df = pd.DataFrame([
        _base_row(enquiry_id="SN-1", project_ref_code="PRJ-1"),
        _base_row(enquiry_id="SN-2", project_ref_code="", enquiry_channel_at_t0="DIRECT_EMAIL"),
    ])
    with sqlite3.connect(str(db_path)) as conn:
        df.to_sql("raw_enquiries", conn, index=False)

    summary = deduplicate_enquiries(db_path=db_path)
    assert summary["clean_count"] == 2
    assert summary["duplicates_count"] == 0
    assert summary["steward_review_count"] == 1

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT enquiry_id_a, enquiry_id_b, status FROM steward_review_queue").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "DATA_STEWARD"


def test_cf09_conflicting_urgency_or_spec_routes_to_steward():
    df = pd.DataFrame([
        _base_row(enquiry_id="SN-1", project_ref_code="PRJ-1", is_urgent_replacement="Urgent"),
        _base_row(enquiry_id="SN-2", project_ref_code="PRJ-1", is_urgent_replacement="Normal"),
    ])
    clean_df, merge_df, steward_df = deduplicate_dataframe_with_steward(df)
    assert len(clean_df) == 2
    assert len(merge_df) == 0
    assert len(steward_df) == 1
    assert steward_df.iloc[0]["status"] == "DATA_STEWARD"

    df2 = pd.DataFrame([
        _base_row(enquiry_id="SN-3", project_ref_code="PRJ-2", spec_completeness="Full"),
        _base_row(enquiry_id="SN-4", project_ref_code="PRJ-2", spec_completeness="Minimal"),
    ])
    clean_df2, merge_df2, steward_df2 = deduplicate_dataframe_with_steward(df2)
    assert len(clean_df2) == 2
    assert len(merge_df2) == 0
    assert len(steward_df2) == 1
    assert steward_df2.iloc[0]["status"] == "DATA_STEWARD"
