"""Tests cho PLPT-57 & OPS-03: Phân bổ theo tài khoản, đo tải Workspace p95 <= 3.0s & telemetry 20 ngày làm việc."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

from src.api.main import app
from src.pilot.pilot_tracker import (
    assign_account_treatment,
    assign_cohort_by_account,
    log_pilot_effort_session,
    summarize_pilot_20bd,
)
from tests.control_fixtures.fixtures_data import create_clean_db, make_cf_row

VN_TZ = timezone(timedelta(hours=7))


def test_plpt57_account_level_assignment_no_conflict():
    assert assign_account_treatment("ACCT-001") == assign_account_treatment("acct-001")

    cohort_df = pd.DataFrame(
        [
            {"enquiry_id": "E1", "account_key": "ACCT-A"},
            {"enquiry_id": "E2", "account_key": "ACCT-A"},
            {"enquiry_id": "E3", "account_key": "ACCT-B"},
            {"enquiry_id": "E4", "account_key": "ACCT-B"},
            {"enquiry_id": "E5", "account_key": "ACCT-C"},
        ]
    )
    assigned_df, summary = assign_cohort_by_account(cohort_df)
    assert summary["total_rows"] == 5
    assert summary["unique_accounts"] == 3
    assert summary["conflicting_accounts"] == 0
    for _, grp in assigned_df.groupby("account_key"):
        assert grp["treatment_arm"].nunique() == 1


def test_plpt57_pilot_20_business_days_effort_telemetry(tmp_path: Path):
    pilot_db = tmp_path / "pilot_20bd.db"
    cur = date(2026, 10, 1)
    logged_days = 0
    while logged_days < 20:
        if cur.weekday() < 5:
            log_pilot_effort_session(
                pilot_db,
                business_date=cur,
                staff_id="sales_01",
                enquiries_reviewed=10,
                queue_prep_minutes=5.0,
                triage_minutes=30.0,
                due_total=10,
                due_on_time=10,
                override_count=1,
            )
            logged_days += 1
        cur += timedelta(days=1)

    summary = summarize_pilot_20bd(
        pilot_db,
        baseline_minutes_per_lead=6.0,
        baseline_on_time_rate=0.95,
    )
    assert summary["observed_business_days"] == 20
    assert summary["min_20bd_met"] is True
    assert summary["avg_minutes_per_lead"] == 3.5
    assert summary["effort_reduction_pct"] > 20.0
    assert summary["effort_target_20pct_met"] is True
    assert summary["due_date_non_inferior"] is True
    assert summary["total_overrides"] == 20


def test_ops03_workspace_queue_p95_under_3s_five_concurrent_sessions(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "perf_workspace.db"
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    rows = [
        make_cf_row(
            enquiry_id=f"PERF-{i:04d}",
            account_key=f"ACCT-{i % 40:03d}",
            t0_time=now_iso,
            extraction_time=now_iso,
            feature_event_time=now_iso,
            feature_available_at=now_iso,
        )
        for i in range(60)
    ]
    create_clean_db(db_path, rows)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "q"))

    client = TestClient(app)

    def _call_once(_idx: int) -> tuple[int, float]:
        t_start = time.perf_counter()
        resp = client.get(
            "/api/v1/workspace/queue?view=active&limit=25",
            headers={"X-User-Role": "SALES_READER", "X-User-Id": f"sales_{_idx % 5}"},
        )
        elapsed = time.perf_counter() - t_start
        return resp.status_code, elapsed

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(_call_once, range(100)))

    statuses = [r[0] for r in results]
    latencies = [r[1] for r in results]
    assert all(code == 200 for code in statuses)
    p95_seconds = float(np.percentile(latencies, 95))
    assert p95_seconds <= 3.0, f"Workspace p95={p95_seconds:.3f}s vượt ngưỡng 3.0s (OPS-03)"
