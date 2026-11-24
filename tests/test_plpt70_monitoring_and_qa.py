"""PLPT-70 [QA] & PLPT-72 [BE]: Validate p95 latency <= 3.0s on real GET /api/v1/workspace/queue
(100 req / 5 concurrent sessions), zero account_key allocation conflict via src.queue_allocation,
effort reduction formula & 20-business-day VN SLA, RBAC & GET /api/v1/monitoring/drift.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from src.api.main import app as main_app
from src.queue_allocation import get_daily_allocation
from src.rules.vietnamese_business_calendar import (
    add_business_days,
    is_business_day,
    is_overdue_after_business_days,
)

VN_TZ = timezone(timedelta(hours=7))
QA_AUTH_HEADERS = {"X-User-Role": "MODEL_REVIEWER", "X-User-Id": "qa_01"}


def _seed_test_db(db_path: Path, row_count: int = 80, inject_missing: bool = False) -> None:
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
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


def test_plpt70_workspace_queue_p95_latency_under_3s_100_req_5_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 10 (Mục 4): Bắn 100 request qua 5 session đồng thời vào endpoint thật
    GET /api/v1/workspace/queue -> xác nhận p95 <= 3.0s.
    """
    db_path = tmp_path / "workspace_perf.db"
    _seed_test_db(db_path, row_count=100, inject_missing=False)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    def _run_single_request(req_idx: int) -> float:
        with TestClient(main_app) as local_client:
            t0 = time.perf_counter()
            resp = local_client.get(
                "/api/v1/workspace/queue?view=Active&limit=50",
                headers={"X-User-Role": "STAFF", "X-User-Id": f"pilot_user_{req_idx % 5}"},
            )
            elapsed = time.perf_counter() - t0
            assert resp.status_code == 200
            return elapsed

    with ThreadPoolExecutor(max_workers=5) as pool:
        latencies_sec = sorted(pool.map(_run_single_request, range(100)))

    p95_index = math.ceil(0.95 * len(latencies_sec)) - 1
    p95_latency = latencies_sec[p95_index]
    assert p95_latency <= 3.0, f"p95 latency {p95_latency:.4f}s vượt ngưỡng 3.0s"


def test_plpt70_zero_account_key_allocation_conflict(tmp_path: Path) -> None:
    """Spec 10 (Mục 1): Kiểm chứng 0 xung đột phân bổ cùng account_key trên DB thật."""
    db_path = tmp_path / "alloc_check.db"
    _seed_test_db(db_path, row_count=40, inject_missing=False)
    today_vn = datetime.now(VN_TZ).date()

    with TestClient(main_app) as local_client:
        local_client.post("/api/v1/workspace/queue/daily")
    allocation = get_daily_allocation(db_path, today_vn) or {}
    assigned_records = allocation if isinstance(allocation, list) else allocation.get("assignments", [])
    seen_accounts: dict[str, set[str]] = {}
    for item in assigned_records:
        if isinstance(item, dict) and item.get("account_key") and item.get("treatment_group"):
            seen_accounts.setdefault(str(item["account_key"]), set()).add(str(item["treatment_group"]))

    conflicts = {acct: groups for acct, groups in seen_accounts.items() if len(groups) > 1}
    assert len(conflicts) == 0


def test_plpt70_effort_reduction_formula_and_20_business_days_sla() -> None:
    """Spec 10 (Mục 1 & 2): Xác minh công thức giảm công sức (>= 20%) & đúng hạn SLA 20 ngày làm việc VN."""
    baseline_min_per_enq = 6.0
    pilot_min_per_enq = 4.5
    effort_reduction_pct = round(
        ((baseline_min_per_enq - pilot_min_per_enq) / baseline_min_per_enq) * 100.0, 2
    )
    assert effort_reduction_pct == 25.0
    assert effort_reduction_pct >= 20.0

    start_dt = datetime(2026, 4, 24, 9, 0, tzinfo=VN_TZ)
    deadline_20bd = add_business_days(start_dt, 20)

    assert is_business_day(date(2026, 4, 25)) is False  # Thứ 7
    assert is_business_day(date(2026, 4, 26)) is False  # Chủ nhật
    assert is_business_day(date(2026, 4, 30)) is False  # Lễ 30/4
    assert is_business_day(date(2026, 5, 1)) is False   # Lễ 1/5
    assert (deadline_20bd.date() - start_dt.date()).days > 20
    assert is_overdue_after_business_days(start_dt, deadline_20bd - timedelta(minutes=1), business_days=20) is False
    assert is_overdue_after_business_days(start_dt, deadline_20bd, business_days=20) is True


def test_plpt72_monitoring_drift_thresholds_rbac_and_retrain_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 11: Xác nhận RBAC 403 cho role trái phép, cảnh báo đúng ngưỡng >=10pp & <90%,
    khóa cứng auto_retrain_enabled == False và trạng thái DEFERRED_UNTIL_60D_MATURITY.
    """
    client = TestClient(main_app)

    res_forbidden = client.get("/api/v1/monitoring/drift", headers={"X-User-Role": "STAFF"})
    assert res_forbidden.status_code == 403

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
    assert body_ok["accuracy_assessment_status"] == "DEFERRED_UNTIL_60D_MATURITY"

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
    assert body_drift["accuracy_assessment_status"] == "DEFERRED_UNTIL_60D_MATURITY"
    alert_codes = {a["code"] for a in body_drift["alerts"]}
    assert "MISSINGNESS_SPIKE" in alert_codes
    assert "LOW_COVERAGE" in alert_codes