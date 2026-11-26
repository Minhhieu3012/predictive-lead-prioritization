"""Tests cho PLPT-58 (OPS-06) & Sprint 5 Ops (OPS-07 Backup/Restore Replay, OPS-08 Portable Exit)."""

import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from src.monitoring.drift_monitor import (
    assess_mature_ranking_drift,
    compute_daily_monitoring_snapshot,
)
from src.ops.backup_restore import (
    create_sqlite_backup,
    restore_backup_with_restriction_replay,
)
from src.ops.portable_exit import export_portable_exit_bundle
from tests.control_fixtures.fixtures_data import create_clean_db, make_cf_row

VN_TZ = timezone(timedelta(hours=7))


def test_plpt58_daily_drift_monitor_and_mature_only_assessment():
    df = pd.DataFrame(
        [
            {
                "enquiry_id": f"M-{i}",
                "product_family": "Motor_DongCo",
                "spec_completeness": "" if i < 2 else "Full",
                "source": "Website" if i % 2 == 0 else "Direct Sales",
            }
            for i in range(10)
        ]
    )
    snap = compute_daily_monitoring_snapshot(
        df,
        reference_missingness_pct={"spec_completeness": 5.0},
        as_of_date="2026-11-22",
    )
    assert snap["daily_volume"] == 10
    assert snap["score_coverage_pct"] == 80.0
    assert snap["investigation_triggered"] is True
    assert snap["recommended_fallback_to_rules"] is True
    assert snap["auto_retrain_allowed"] is False

    alert_codes = {a["code"] for a in snap["alerts"]}
    assert "MISSINGNESS_SPIKE" in alert_codes
    assert "LOW_COVERAGE" in alert_codes

    # Chỉ đánh giá ranking drift trên nhãn đã chín >= 60 ngày
    enq_df = pd.DataFrame(
        [
            {"enquiry_id": "OLD-1", "t0_time": "2026-08-01T08:00:00+07:00"},
            {"enquiry_id": "NEW-1", "t0_time": "2026-11-15T08:00:00+07:00"},
        ]
    )
    out_df = pd.DataFrame(
        [
            {"enquiry_id": "OLD-1", "label": "POSITIVE"},
            {"enquiry_id": "NEW-1", "label": "Unknown"},
        ]
    )
    drift = assess_mature_ranking_drift(
        enq_df,
        out_df,
        as_of_dt=datetime(2026, 11, 22, 10, 0, tzinfo=VN_TZ),
    )
    assert drift["mature_eligible_count"] == 1
    assert drift["immature_excluded_count"] == 1
    assert drift["ranking_drift_assessable"] is True
    assert drift["auto_retrain_allowed"] is False


def test_sprint5_ops_backup_restore_replay_and_portable_exit(tmp_path: Path):
    src_db = tmp_path / "source.db"
    create_clean_db(
        src_db,
        [
            make_cf_row(enquiry_id="OP-001", account_key="ACCT-OP-1"),
            make_cf_row(enquiry_id="OP-002", account_key="ACCT-OP-2"),
        ],
    )

    backup_meta = create_sqlite_backup(src_db, tmp_path / "backups")
    assert Path(backup_meta["backup_file"]).exists()
    assert backup_meta["retention_days"] == 35
    assert backup_meta["rto_hours"] == 4
    assert backup_meta["rpo_hours"] == 24

    restored_db = tmp_path / "restored.db"
    restore_res = restore_backup_with_restriction_replay(
        backup_meta["backup_file"],
        restored_db,
        expected_sha256=backup_meta["sha256"],
        restriction_events=[
            {"action": "BLOCK", "enquiry_id": "OP-001", "reason": "Khach rut dong y lien he"},
            {"action": "DELETE", "enquiry_id": "OP-002"},
        ],
    )
    assert restore_res["status"] == "RESTORED_AND_VERIFIED"
    assert restore_res["replayed_blocks"] == 1
    assert restore_res["replayed_deletions"] == 1

    with contextlib.closing(sqlite3.connect(str(restored_db))) as conn:
        enq_ids = [r[0] for r in conn.execute("SELECT enquiry_id FROM clean_enquiries").fetchall()]
        blk_ids = [r[0] for r in conn.execute("SELECT enquiry_id FROM blocked_accounts").fetchall()]
        assert enq_ids == ["OP-001"]
        assert blk_ids == ["OP-001"]

    exit_bundle = export_portable_exit_bundle(restored_db, tmp_path / "exit_export")
    assert Path(exit_bundle["manifest_file"]).exists()
    assert "clean_enquiries" in exit_bundle["tables"]
    assert "blocked_accounts" in exit_bundle["tables"]
    assert exit_bundle["tables"]["clean_enquiries"]["row_count"] == 1



def test_plpt74_daily_drift_snapshot_persistence_and_no_auto_retrain(tmp_path: Path):
    from src.monitoring.drift_monitor import persist_daily_drift_snapshot

    db_path = tmp_path / "plpt74.db"
    df = pd.DataFrame(
        [
            {"enquiry_id": f"D-{i}", "product_family": "Motor_DongCo", "spec_completeness": "" if i < 2 else "Full", "source": "Website"}
            for i in range(10)
        ]
    )
    snap = compute_daily_monitoring_snapshot(df, reference_missingness_pct={"spec_completeness": 5.0}, as_of_date="2026-11-26")
    row_id = persist_daily_drift_snapshot(str(db_path), snap)
    assert row_id == 1

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        row = conn.execute("SELECT score_coverage_pct, investigation_triggered, auto_retrain_allowed FROM daily_drift_snapshots WHERE id = 1").fetchone()
        assert row == (80.0, 1, 0)


def test_plpt74_http_monitoring_drift_rbac_and_thresholds(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.security.auth import DEFAULT_SESSION_STORE

    db_path = tmp_path / "plpt74_http.db"
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    rows = [
        make_cf_row(
            enquiry_id=f"DR-{i}",
            account_key=f"ACCT-{i}",
            spec_completeness="Minimal" if i < 2 else "Full",
            t0_time=now_iso,
            extraction_time=now_iso,
            feature_event_time=now_iso,
            feature_available_at=now_iso,
        )
        for i in range(10)
    ]
    create_clean_db(db_path, rows)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "q"))
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    DEFAULT_SESSION_STORE._sessions.clear()

    client = TestClient(app)
    assert client.get("/api/v1/monitoring/drift").status_code == 401
    sales_tok = client.post("/auth/login", json={"user_id": "sales_74", "role": "SALES_READER"}).json()["token"]
    rev_tok = client.post("/auth/login", json={"user_id": "rev_74", "role": "MODEL_REVIEWER"}).json()["token"]

    assert client.get("/api/v1/monitoring/drift", headers={"Authorization": f"Bearer {sales_tok}"}).status_code == 403
    r_mon = client.get("/api/v1/monitoring/drift", headers={"Authorization": f"Bearer {rev_tok}"})
    assert r_mon.status_code == 200
    data = r_mon.json()
    assert data["score_coverage_pct"] == 80.0
    assert data["investigation_triggered"] is True
    assert data["auto_retrain_enabled"] is False
    assert any(a["code"] == "LOW_COVERAGE" for a in data["alerts"])
