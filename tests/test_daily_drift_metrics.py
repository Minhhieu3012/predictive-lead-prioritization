import json
import sqlite3

import pandas as pd

from src.monitoring.drift_monitor import (
    compute_daily_monitoring_snapshot,
    persist_monitoring_snapshot,
)


def _make_enquiries():
    return pd.DataFrame({
        "enquiry_id": [f"E{i:03}" for i in range(1, 11)],
        "spec_completeness": [1.0] * 10,
        "product_family": ["A"] * 9 + [None],
        "workflow_state": ["SCORED"] * 9 + ["UNSCORED"],
        "enquiry_channel_at_t0": ["Email"] * 6 + ["Web"] * 4,
    })


def test_daily_snapshot_calculates_volume_and_feature_missingness():
    snapshot = compute_daily_monitoring_snapshot(
        _make_enquiries(),
        reference_missingness_pct={
            "spec_completeness": 0.0,
            "product_family": 0.0,
        },
        as_of_date="2026-11-24",
    )

    assert snapshot["daily_volume"] == 10
    assert snapshot["as_of_date"] == "2026-11-24"
    assert snapshot["missingness_pct"]["spec_completeness"] == 0.0
    assert snapshot["missingness_pct"]["product_family"] == 10.0

    product_family = next(
        row for row in snapshot["missingness_table"]
        if row["feature"] == "product_family"
    )
    assert product_family["delta_pp"] == 10.0
    assert product_family["is_drift_breach"] is True


def test_daily_snapshot_calculates_score_coverage():
    snapshot = compute_daily_monitoring_snapshot(
        _make_enquiries(),
        reference_missingness_pct={
            "spec_completeness": 0.0,
            "product_family": 0.0,
        },
        as_of_date="2026-11-24",
    )

    # One of ten records is UNSCORED.
    assert snapshot["score_coverage_pct"] == 90.0
    assert snapshot["unscored_rate_pct"] == 10.0
    assert not any(
        alert["code"] == "LOW_COVERAGE"
        for alert in snapshot["alerts"]
    )


def test_daily_snapshot_calculates_channel_mix():
    snapshot = compute_daily_monitoring_snapshot(
        _make_enquiries(),
        reference_missingness_pct={
            "spec_completeness": 0.0,
            "product_family": 0.0,
        },
        as_of_date="2026-11-24",
    )

    assert snapshot["channel_mix_pct"]["Email"] == 60.0
    assert snapshot["channel_mix_pct"]["Web"] == 40.0


def test_persist_monitoring_snapshot_saves_snapshot_and_alerts(tmp_path):
    db_path = tmp_path / "monitoring.db"
    sqlite3.connect(db_path).close()

    snapshot = {
        "as_of_date": "2026-11-24",
        "daily_volume": 10,
        "score_coverage_pct": 80.0,
        "missingness_table": [
            {
                "feature": "product_family",
                "baseline_missing_pct": 0.0,
                "current_missing_pct": 10.0,
                "delta_pp": 10.0,
                "is_drift_breach": True,
            }
        ],
        "channel_mix_pct": {"Email": 60.0, "Web": 40.0},
    }
    alerts = [
        {
            "code": "MISSINGNESS_SPIKE",
            "feature": "product_family",
            "message": "Missingness increased by 10 pp.",
        },
        {
            "code": "LOW_COVERAGE",
            "message": "Score coverage is below 90%.",
        },
    ]

    persist_monitoring_snapshot(db_path, snapshot, alerts)

    with sqlite3.connect(db_path) as conn:
        saved_snapshot = conn.execute(
            "SELECT snapshot_date, daily_volume, score_coverage_pct, "
            "missingness_json, channel_mix_json "
            "FROM daily_drift_snapshots"
        ).fetchone()

        saved_alerts = conn.execute(
            "SELECT alert_code, feature_name, status "
            "FROM drift_investigation_alerts ORDER BY id"
        ).fetchall()

    assert saved_snapshot[0] == "2026-11-24"
    assert saved_snapshot[1] == 10
    assert saved_snapshot[2] == 80.0
    assert json.loads(saved_snapshot[3])[0]["feature"] == "product_family"
    assert json.loads(saved_snapshot[4]) == {"Email": 60.0, "Web": 40.0}

    assert len(saved_alerts) == 2
    assert saved_alerts[0] == ("MISSINGNESS_SPIKE", "product_family", "OPEN")
    assert saved_alerts[1] == ("LOW_COVERAGE", None, "OPEN")
