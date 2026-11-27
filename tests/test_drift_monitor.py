import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.monitoring.drift_monitor import (
    assess_mature_ranking_drift,
    load_reference_baseline,
)


def test_load_reference_baseline_reads_feature_missing_pct(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({
            "baseline_version": "v1.0",
            "feature_missing_pct": {
                "product_family": 5.0,
                "spec_completeness": 10.0,
            },
        }),
        encoding="utf-8",
    )

    baseline = load_reference_baseline(
        columns=["product_family", "spec_completeness", "industry_at_t0"],
        baseline_path=baseline_path,
    )

    assert baseline["product_family"] == 5.0
    assert baseline["spec_completeness"] == 10.0
    assert baseline["industry_at_t0"] == 0.0


def test_load_reference_baseline_preserves_missing_reference(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"feature_missing_pct": {"product_family": 3.0}}),
        encoding="utf-8",
    )

    baseline = load_reference_baseline(
        columns=["product_family", "industry_at_t0"],
        baseline_path=baseline_path,
    )

    assert baseline["product_family"] == 3.0
    assert baseline["industry_at_t0"] == 0.0


def test_ranking_drift_defers_when_records_are_not_60_days_mature():
    as_of_dt = datetime(2026, 11, 24, tzinfo=timezone.utc)
    enquiries = pd.DataFrame({
        "enquiry_id": ["E001", "E002"],
        "t0_time": [
            (as_of_dt - timedelta(days=59)).isoformat(),
            (as_of_dt - timedelta(days=30)).isoformat(),
        ],
    })
    outcomes = pd.DataFrame({
        "enquiry_id": ["E001", "E002"],
        "label": [1, 0],
    })

    result = assess_mature_ranking_drift(enquiries, outcomes, as_of_dt=as_of_dt)

    assert result["status"] == "DEFERRED_UNTIL_60D_MATURITY"
    assert result["mature_eligible_count"] == 0
    assert result["immature_excluded_count"] == 2
    assert result["ranking_drift_assessable"] is False
    assert result["auto_retrain_allowed"] is False
    assert result["auto_retrain_enabled"] is False


def test_ranking_drift_assesses_only_mature_records_with_available_labels():
    as_of_dt = datetime(2026, 11, 24, tzinfo=timezone.utc)
    enquiries = pd.DataFrame({
        "enquiry_id": ["E001", "E002", "E003"],
        "t0_time": [
            (as_of_dt - timedelta(days=60)).isoformat(),
            (as_of_dt - timedelta(days=90)).isoformat(),
            (as_of_dt - timedelta(days=59)).isoformat(),
        ],
    })
    outcomes = pd.DataFrame({
        "enquiry_id": ["E001"],
        "label": [1],
    })

    result = assess_mature_ranking_drift(enquiries, outcomes, as_of_dt=as_of_dt)

    assert result["status"] == "READY"
    assert result["mature_eligible_count"] == 2
    assert result["immature_excluded_count"] == 1
    assert result["known_mature_count"] == 1
    assert result["unknown_mature_count"] == 1
    assert result["mature_label_coverage_pct"] == 50.0
    assert result["ranking_drift_assessable"] is True
    assert result["auto_retrain_allowed"] is False
    assert result["auto_retrain_enabled"] is False


def test_ranking_drift_defers_when_mature_records_have_no_outcomes():
    as_of_dt = datetime(2026, 11, 24, tzinfo=timezone.utc)
    enquiries = pd.DataFrame({
        "enquiry_id": ["E001"],
        "t0_time": [(as_of_dt - timedelta(days=90)).isoformat()],
    })
    outcomes = pd.DataFrame(columns=["enquiry_id", "label"])

    result = assess_mature_ranking_drift(enquiries, outcomes, as_of_dt=as_of_dt)

    assert result["status"] == "DEFERRED_UNTIL_60D_MATURITY"
    assert result["mature_eligible_count"] == 1
    assert result["known_mature_count"] == 0
    assert result["auto_retrain_allowed"] is False
    assert result["auto_retrain_enabled"] is False

