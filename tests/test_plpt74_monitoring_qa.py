import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.main import app as main_app
from src.models import train_candidates
from src.monitoring.drift_monitor import compute_daily_monitoring_snapshot


MONITORING_URL = "/api/v1/monitoring/drift"

ALLOWED_ROLES = [
    "MODEL_REVIEWER",
    "DATA_STEWARD",
    "SUPERVISOR",
    "ADMINISTRATOR",
]


def _make_monitoring_frame(
    total_rows: int = 100,
    missing_product_family: int = 0,
    unscored_rows: int = 0,
) -> pd.DataFrame:
    """Tạo dữ liệu kiểm thử ngưỡng missingness và score coverage."""
    return pd.DataFrame(
        {
            "product_family": [
                None if i < missing_product_family else "INDUSTRIAL_VALVE"
                for i in range(total_rows)
            ],
            "spec_completeness": [0.95] * total_rows,
            "workflow_state": [
                "UNSCORED" if i < unscored_rows else "OPEN"
                for i in range(total_rows)
            ],
        }
    )


def _baseline() -> dict[str, float]:
    """Baseline missingness bằng 0% để kiểm tra chính xác mức tăng."""
    return {
        "product_family": 0.0,
        "spec_completeness": 0.0,
    }


def _seed_monitoring_db(db_path) -> None:
    """Tạo clean_enquiries tối thiểu để kiểm thử API monitoring."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE clean_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT,
                cohort_id TEXT,
                owner_id TEXT,
                t0_time TEXT,
                extraction_time TEXT,
                product_family TEXT,
                workflow_state TEXT,
                spec_completeness REAL
            )
            """
        )

        rows = [
            (
                f"SN-{i:04d}",
                f"ACCT-{i:04d}",
                "COHORT_A",
                f"OWNER-{i % 4}",
                now_iso,
                now_iso,
                "INDUSTRIAL_VALVE",
                "OPEN",
                0.95,
            )
            for i in range(20)
        ]

        conn.executemany(
            """
            INSERT INTO clean_enquiries
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def test_missingness_increase_at_exactly_10_percentage_points_triggers_alert():
    frame = _make_monitoring_frame(
        total_rows=100,
        missing_product_family=10,
    )

    result = compute_daily_monitoring_snapshot(
        frame,
        reference_missingness_pct=_baseline(),
        as_of_date="2026-11-24",
    )

    alerts = [
        alert
        for alert in result["alerts"]
        if alert["code"] == "MISSINGNESS_SPIKE"
        and alert.get("feature") == "product_family"
    ]

    assert result["investigation_triggered"] is True
    assert len(alerts) == 1
    assert alerts[0]["delta_pp"] == 10.0
    assert alerts[0]["threshold_pp"] == 10.0


def test_missingness_increase_below_10_percentage_points_does_not_alert():
    frame = _make_monitoring_frame(
        total_rows=100,
        missing_product_family=9,
    )

    result = compute_daily_monitoring_snapshot(
        frame,
        reference_missingness_pct=_baseline(),
        as_of_date="2026-11-24",
    )

    alerts = [
        alert
        for alert in result["alerts"]
        if alert["code"] == "MISSINGNESS_SPIKE"
        and alert.get("feature") == "product_family"
    ]

    assert len(alerts) == 0
    assert result["investigation_triggered"] is False


def test_score_coverage_at_exactly_90_percent_does_not_alert():
    frame = _make_monitoring_frame(
        total_rows=100,
        unscored_rows=10,
    )

    result = compute_daily_monitoring_snapshot(
        frame,
        reference_missingness_pct=_baseline(),
        as_of_date="2026-11-24",
    )

    coverage_alerts = [
        alert
        for alert in result["alerts"]
        if alert["code"] == "LOW_COVERAGE"
    ]

    assert result["score_coverage_pct"] == 90.0
    assert len(coverage_alerts) == 0


def test_score_coverage_below_90_percent_triggers_alert():
    frame = _make_monitoring_frame(
        total_rows=100,
        unscored_rows=11,
    )

    result = compute_daily_monitoring_snapshot(
        frame,
        reference_missingness_pct=_baseline(),
        as_of_date="2026-11-24",
    )

    coverage_alerts = [
        alert
        for alert in result["alerts"]
        if alert["code"] == "LOW_COVERAGE"
    ]

    assert result["score_coverage_pct"] == 89.0
    assert len(coverage_alerts) == 1
    assert coverage_alerts[0]["threshold_pct"] == 90.0


@pytest.mark.parametrize("role", ALLOWED_ROLES)
def test_authorized_roles_can_access_monitoring_dashboard(
    role,
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    with TestClient(main_app) as client:
        response = client.get(
            MONITORING_URL,
            headers={
                "X-User-Role": role,
                "X-User-Id": "qa_plpt74",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "NO_DATA"


def test_unauthorized_role_is_denied_monitoring_dashboard(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    with TestClient(main_app) as client:
        response = client.get(
            MONITORING_URL,
            headers={
                "X-User-Role": "STAFF",
                "X-User-Id": "qa_plpt74",
            },
        )

    assert response.status_code == 403


def test_daily_monitoring_never_triggers_model_training(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "monitoring.db"
    _seed_monitoring_db(db_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    def fail_if_training_called(*args, **kwargs):
        pytest.fail("Monitoring must never trigger model training")

    monkeypatch.setattr(
        train_candidates,
        "run_training",
        fail_if_training_called,
    )

    with TestClient(main_app) as client:
        response = client.get(
            MONITORING_URL,
            headers={
                "X-User-Role": "MODEL_REVIEWER",
                "X-User-Id": "qa_plpt74",
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["auto_retrain_allowed"] is False
    assert body["auto_retrain_enabled"] is False
    assert body["daily_snapshot"]["daily_volume"] == 20
    assert body["accuracy_assessment_status"] == "DEFERRED_UNTIL_60D_MATURITY"
    assert body["mature_drift"]["auto_retrain_allowed"] is False
    assert body["mature_drift"]["auto_retrain_enabled"] is False
