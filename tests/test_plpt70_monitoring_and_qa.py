"""PLPT-70 [QA]: Validate p95 latency <= 3.0s, zero account_key allocation conflict,
20-business-day VN calendar SLA logic, and PLPT-72 drift monitoring API.
"""

from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.monitoring import router as monitoring_router
from src.config.vn_holidays import count_vn_business_days, is_vn_business_day
from src.services.queue_service import build_workspace_queue_payload

VN_TZ = timezone(timedelta(hours=7))
QA_AUTH_HEADERS = {"X-User-Role": "MODEL_REVIEWER", "X-User-Id": "qa_01"}


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(monitoring_router)
    return test_app


def _seed_test_db(db_path: Path, row_count: int = 60, inject_missing: bool = False) -> None:
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
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
                is_urgent_replacement TEXT,
                has_contractual_sla TEXT,
                spec_completeness REAL,
                estimated_value_vnd REAL
            )
            """
        )
        rows = []
        for idx in range(row_count):
            acct = f"ACCT-{idx:04d}"
            prod = "INDUSTRIAL_VALVE"
            spec_val: float | None = 0.95
            if inject_missing and idx % 2 == 0:
                prod = ""
                spec_val = None
            rows.append(
                (
                    f"SN-{idx:04d}",
                    acct,
                    "COHORT_A" if idx % 2 == 0 else "COHORT_B",
                    f"OWNER-{idx % 4}",
                    now_iso,
                    now_iso,
                    prod,
                    "OPEN",
                    "false",
                    "false",
                    spec_val,
                    250_000_000.0,
                )
            )
        conn.executemany(
            "INSERT INTO clean_enquiries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def test_plpt70_api_p95_latency_under_3s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Xác nhận p95 latency của workspace queue + /api/v1/monitoring/drift <= 3.0s."""
    db_path = tmp_path / "perf.db"
    _seed_test_db(db_path, row_count=80, inject_missing=False)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    client = TestClient(_build_test_app())
    latencies_sec: list[float] = []

    for _ in range(20):
        t0 = time.perf_counter()
        _ = build_workspace_queue_payload(view="active", include_overflow=False)
        resp = client.get("/api/v1/monitoring/drift", headers=QA_AUTH_HEADERS)
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        latencies_sec.append(elapsed)

    latencies_sec.sort()
    p95_index = math.ceil(0.95 * len(latencies_sec)) - 1
    p95_latency = latencies_sec[p95_index]
    assert p95_latency <= 3.0, f"p95 latency {p95_latency:.4f}s vượt ngưỡng 3.0s"


def test_plpt70_zero_account_key_allocation_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Xác nhận active queue không có xung đột enquiry_id hoặc account_key trùng lặp."""
    db_path = tmp_path / "alloc.db"
    _seed_test_db(db_path, row_count=40, inject_missing=False)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    payload = build_workspace_queue_payload(view="unscored", include_overflow=True)
    items = payload.get("entries") or payload.get("items") or payload.get("queue") or []
    assert len(items) > 0

    enq_ids = [item["enquiry_id"] for item in items if item.get("enquiry_id")]
    acct_keys = [item["account_key"] for item in items if item.get("account_key")]
    assert len(enq_ids) == len(set(enq_ids))
    assert len(acct_keys) == len(set(acct_keys))


def test_plpt70_sla_20_business_days_window() -> None:
    """Xác nhận cửa sổ SLA 20 ngày làm việc VN (loại trừ T7/CN và ngày lễ VN)."""
    start_day = date(2026, 4, 24)
    start_dt = datetime(2026, 4, 24, 9, 0, tzinfo=VN_TZ)
    end_dt = datetime(2026, 5, 27, 9, 0, tzinfo=VN_TZ)

    assert is_vn_business_day(date(2026, 4, 27)) is False
    assert is_vn_business_day(date(2026, 4, 30)) is False
    assert is_vn_business_day(date(2026, 5, 1)) is False
    assert (end_dt.date() - start_day).days > 20
    assert count_vn_business_days(start_dt, end_dt) == 20


def test_plpt72_monitoring_drift_thresholds_and_retrain_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Xác nhận GET /api/v1/monitoring/drift cảnh báo đúng ngưỡng và auto_retrain_allowed == False."""
    client = TestClient(_build_test_app())

    db_healthy = tmp_path / "healthy.db"
    _seed_test_db(db_healthy, row_count=20, inject_missing=False)
    monkeypatch.setenv("DATABASE_PATH", str(db_healthy))

    res_ok = client.get("/api/v1/monitoring/drift", headers=QA_AUTH_HEADERS)
    assert res_ok.status_code == 200
    body_ok = res_ok.json()
    assert body_ok["status"] == "HEALTHY"
    assert body_ok["drift_detected"] is False
    assert body_ok["auto_retrain_allowed"] is False
    assert body_ok["auto_retrain_enabled"] is False

    db_drift = tmp_path / "drift.db"
    _seed_test_db(db_drift, row_count=20, inject_missing=True)
    monkeypatch.setenv("DATABASE_PATH", str(db_drift))

    res_drift = client.get("/api/v1/monitoring/drift", headers=QA_AUTH_HEADERS)
    assert res_drift.status_code == 200
    body_drift = res_drift.json()
    assert body_drift["status"] == "DRIFT_DETECTED"
    assert body_drift["drift_detected"] is True
    assert body_drift["auto_retrain_allowed"] is False
    assert body_drift["auto_retrain_enabled"] is False
    alert_codes = {a["code"] for a in body_drift["alerts"]}
    assert "MISSINGNESS_SPIKE" in alert_codes
    assert "LOW_COVERAGE" in alert_codes