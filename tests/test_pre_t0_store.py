from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from src.ml.pre_t0_store import (
    FEATURE_COLUMNS,
    MAX_FEATURE_COLUMNS,
    build_pre_t0_records,
    build_pre_t0_store,
)

VN_TZ = timezone(timedelta(hours=7))


def _enquiry(enquiry_id, account_key, t0_time, **overrides):
    row = {
        "enquiry_id": enquiry_id,
        "account_key": account_key,
        "t0_time": t0_time,
        "product_family": "Motor_DongCo",
        "spec_completeness": "Full",
        "is_urgent_replacement": "Normal",
        "contract_due_date": "2026-12-01",
        "industry_at_t0": "Manufacturing",
        "enquiry_channel_at_t0": "Email",
        "region_at_t0": "North",
        "account_age_days_at_t0": "365",
        "prior_purchase_count_at_t0": "2",
        "estimated_value_band_at_t0": "Medium",
        "feature_event_time": t0_time,
        "feature_available_at": t0_time,
        "contact_phone_DO_NOT_USE": "0900000000",
        "free_text_note": "sensitive free text",
    }
    row.update(overrides)
    return row


def test_point_in_time_boundary_and_safe_feature_schema():
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=VN_TZ).isoformat()
    features, lineage = build_pre_t0_records([_enquiry("SN-1", "ACCT-1", t0)])

    assert len(FEATURE_COLUMNS) <= MAX_FEATURE_COLUMNS
    assert len(features) == 1
    assert features[0]["enquiry_id"] == "SN-1"
    assert features[0]["account_history_status"] == "NO_PRIOR_ENQUIRY_OBSERVED"
    assert features[0]["prior_enquiry_count"] is None
    assert "account_key" not in features[0]
    assert "contact_phone_DO_NOT_USE" not in features[0]
    assert "free_text_note" not in features[0]
    assert lineage[0]["feature_status"] == "READY"


@pytest.mark.parametrize(
    ("timestamp_field", "timestamp_value", "expected_code"),
    [
        (
            "feature_event_time",
            "2026-11-19T12:00:01+07:00",
            "FEATURE_EVENT_AFTER_T0",
        ),
        (
            "feature_available_at",
            "2026-11-19T12:00:01+07:00",
            "FEATURE_AVAILABLE_AFTER_T0",
        ),
        ("feature_event_time", "not-a-timestamp", "INVALID_FEATURE_EVENT_TIME"),
        ("feature_available_at", "", "INVALID_FEATURE_AVAILABLE_AT"),
    ],
)
def test_rejects_features_with_invalid_or_post_t0_timestamps(
    timestamp_field, timestamp_value, expected_code
):
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=VN_TZ).isoformat()
    features, lineage = build_pre_t0_records(
        [_enquiry("SN-1", "ACCT-1", t0, **{timestamp_field: timestamp_value})]
    )

    assert features == []
    assert lineage[0]["feature_status"] == "REJECTED"
    assert lineage[0]["rejection_code"] == expected_code


def test_aggregates_only_eligible_prior_account_enquiries():
    first_t0 = datetime(2026, 11, 17, 9, 0, tzinfo=VN_TZ)
    second_t0 = datetime(2026, 11, 18, 9, 0, tzinfo=VN_TZ)
    future_t0 = datetime(2026, 11, 20, 9, 0, tzinfo=VN_TZ)
    rows = [
        _enquiry(
            "SN-1",
            "ACCT-1",
            first_t0.isoformat(),
            product_family="Motor_DongCo",
        ),
        _enquiry(
            "SN-2",
            "ACCT-1",
            second_t0.isoformat(),
            product_family="Drive_BienTan",
        ),
        _enquiry(
            "SN-FUTURE",
            "ACCT-1",
            future_t0.isoformat(),
            product_family="Transmission_HopSo",
        ),
        _enquiry(
            "SN-POST-AVAILABLE",
            "ACCT-1",
            second_t0.isoformat(),
            feature_available_at=future_t0.isoformat(),
        ),
    ]

    features, _ = build_pre_t0_records(rows)
    by_id = {record["enquiry_id"]: record for record in features}

    assert by_id["SN-1"]["prior_enquiry_count"] is None
    assert by_id["SN-2"]["prior_enquiry_count"] == 1
    assert by_id["SN-2"]["prior_distinct_product_family_count"] == 1
    assert by_id["SN-2"]["account_history_status"] == "HISTORY_FOUND"
    assert by_id["SN-FUTURE"]["prior_enquiry_count"] == 2
    assert "SN-POST-AVAILABLE" not in by_id


def test_missing_account_key_stays_unknown():
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=VN_TZ).isoformat()
    features, _ = build_pre_t0_records([_enquiry("SN-1", "", t0)])

    assert features[0]["account_history_status"] == "UNKNOWN"
    assert features[0]["prior_enquiry_count"] is None


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("account_age_days_at_t0", "-1", "INVALID_ACCOUNT_AGE"),
        (
            "prior_purchase_count_at_t0",
            "1.5",
            "INVALID_PRIOR_PURCHASE_COUNT",
        ),
    ],
)
def test_rejects_invalid_account_count_fields(field, value, expected_code):
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=VN_TZ).isoformat()
    features, lineage = build_pre_t0_records(
        [_enquiry("SN-1", "ACCT-1", t0, **{field: value})]
    )

    assert features == []
    assert lineage[0]["rejection_code"] == expected_code


def test_store_persists_no_account_id_or_unapproved_fields(tmp_path):
    db_path = tmp_path / "store.db"
    t0 = datetime.now(VN_TZ).replace(microsecond=0)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE clean_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT,
                t0_time TEXT,
                product_family TEXT,
                spec_completeness TEXT,
                is_urgent_replacement TEXT,
                contract_due_date TEXT,
                industry_at_t0 TEXT,
                enquiry_channel_at_t0 TEXT,
                region_at_t0 TEXT,
                account_age_days_at_t0 INTEGER,
                prior_purchase_count_at_t0 INTEGER,
                estimated_value_band_at_t0 TEXT,
                feature_event_time TEXT,
                feature_available_at TEXT,
                contact_phone_DO_NOT_USE TEXT,
                free_text_note TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO clean_enquiries VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "SN-1",
                "ACCT-SENSITIVE",
                t0.isoformat(),
                "Motor_DongCo",
                "Full",
                "Normal",
                "2026-12-01",
                "Manufacturing",
                "Email",
                "North",
                365,
                2,
                "Medium",
                t0.isoformat(),
                t0.isoformat(),
                "0900000000",
                "do not persist",
            ),
        )

    result = build_pre_t0_store(db_path)

    assert result["feature_count"] == 1
    assert result["rejected_count"] == 0
    with sqlite3.connect(db_path) as conn:
        feature_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(pre_t0_features)")
        }
        lineage_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(pre_t0_feature_lineage)")
        }
        stored_feature = conn.execute(
            "SELECT * FROM pre_t0_features WHERE enquiry_id='SN-1'"
        ).fetchone()
        stored_lineage = conn.execute(
            "SELECT * FROM pre_t0_feature_lineage WHERE enquiry_id='SN-1'"
        ).fetchone()

    assert "account_key" not in feature_columns | lineage_columns
    assert "contact_phone_DO_NOT_USE" not in feature_columns | lineage_columns
    assert "free_text_note" not in feature_columns | lineage_columns
    assert len(feature_columns) - 3 <= MAX_FEATURE_COLUMNS
    assert "ACCT-SENSITIVE" not in repr(stored_feature)
    assert "0900000000" not in repr(stored_feature)
    assert "do not persist" not in repr(stored_feature)
    assert stored_lineage is not None
