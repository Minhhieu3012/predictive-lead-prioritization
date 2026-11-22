"""Tests cho cấu trúc chuẩn hóa Rules-First (PLPT-56, PLPT-57, PLPT-58, Security & Ops Sprint 4-5)."""

import contextlib
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException
import numpy as np
import pandas as pd
import pytest

from src.features.pre_t0_store import LeakageError, build_pre_t0_features
from src.models.serving_router import (
    DEFAULT_SERVING_MODE,
    FALLBACK_SERVING_MODE,
    PREDICTIVE_SERVING_MODE,
    resolve_serving_route,
    route_queue_scoring,
)
from src.models.shadow_runner import SHADOW_SCORING_MODE, run_shadow_batch
from src.models.train_candidates import RegularizedLogisticRegressionModel, TabularPreprocessor
from src.monitoring.drift_monitor import (
    assess_mature_ranking_drift,
    compute_daily_monitoring_snapshot,
)
from src.ops.backup_restore import (
    create_sqlite_backup,
    restore_backup_with_restriction_replay,
)
from src.ops.portable_exit import export_portable_exit_bundle
from src.pilot.pilot_tracker import (
    assign_account_treatment,
    assign_cohort_by_account,
    log_pilot_effort_session,
    summarize_pilot_20bd,
)
from src.security.auth import MAX_CONCURRENT_SESSIONS, SessionStore, resolve_auth_context
from src.security.incident_hold import (
    HOLD_STALE_DATA,
    activate_emergency_hold,
    commit_local_emergency_block,
    get_active_incident_hold,
    record_purchaser_notification,
    release_emergency_hold,
)
from src.security.rbac import VALID_ROLES, has_permission
from tests.control_fixtures.fixtures_data import create_clean_db, make_cf_row

VN_TZ = timezone(timedelta(hours=7))


def test_shadow_runner_and_serving_router_rules_first(tmp_path: Path):
    db_path = tmp_path / "shadow.db"
    row = make_cf_row(enquiry_id="SH-001", account_key="ACCT-SH-01")
    create_clean_db(db_path, [row])

    feat_df, cols = build_pre_t0_features(pd.DataFrame([row]), strict_leakage=True)
    prep = TabularPreprocessor(cols).fit(feat_df)
    model = RegularizedLogisticRegressionModel().fit(prep.transform(feat_df), np.array([1.0]))

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_version": "plpt-logreg-v1.0.0",
                "approval_status": "PENDING_GATE_G1_REVIEW",
                "serving_mode_locked": "RULES_BASELINE",
            }
        ),
        encoding="utf-8",
    )

    shadow_df = run_shadow_batch(db_path, manifest_path, model, prep, persist_logs=True)
    assert len(shadow_df) == 1
    assert shadow_df.iloc[0]["scoring_mode"] == SHADOW_SCORING_MODE
    assert shadow_df.iloc[0]["serving_mode_locked"] == "RULES_BASELINE"
    assert bool(shadow_df.iloc[0]["is_visible_to_staff"]) is False

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        logged = conn.execute("SELECT COUNT(*) FROM shadow_score_logs").fetchone()[0]
        assert logged == 1

    with pytest.raises(LeakageError):
        run_shadow_batch(
            db_path,
            manifest_path,
            model,
            prep,
            requested_tables=["clean_enquiries", "raw_outcomes"],
        )

    # Mặc định router trả RULES_BASELINE
    default_route = resolve_serving_route(manifest=manifest_path)
    assert default_route.active_mode == DEFAULT_SERVING_MODE
    assert default_route.rules_only_enforced is True
    assert default_route.fallback_triggered is False

    # Yêu cầu PREDICTIVE_ML nhưng Gate G1 chưa duyệt / SYNTHETIC -> tự động fallback
    fallback_route = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "FALLBACK_RULES_BASELINE", "data_origin": "SYNTHETIC"},
        manifest=manifest_path,
    )
    assert fallback_route.active_mode == FALLBACK_SERVING_MODE
    assert fallback_route.fallback_triggered is True
    assert fallback_route.rules_only_enforced is True

    # Gate G1 APPROVED trên REAL nhưng manifest chưa APPROVED -> vẫn fallback
    locked_route = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
        manifest=manifest_path,
    )
    assert locked_route.active_mode == FALLBACK_SERVING_MODE
    assert locked_route.fallback_triggered is True

    # Gate G1 APPROVED trên REAL + manifest APPROVED + controls OK -> mở PREDICTIVE_ML
    approved_route = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
        manifest={"model_version": "v2", "approval_status": "APPROVED", "serving_mode_locked": "UNLOCKED"},
    )
    assert approved_route.active_mode == PREDICTIVE_SERVING_MODE
    assert approved_route.fallback_triggered is False

    # Drift / Incident hold buộc fallback ngay lập tức
    drift_fb = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
        manifest={"model_version": "v2", "approval_status": "APPROVED", "serving_mode_locked": "UNLOCKED"},
        drift_fallback_triggered=True,
    )
    assert drift_fb.active_mode == FALLBACK_SERVING_MODE
    assert drift_fb.fallback_reason == "DRIFT_THRESHOLD_BREACHED"

    routed = route_queue_scoring([row], route_decision=default_route, shadow_scores_by_id={"SH-001": 0.77})
    assert routed["route"]["active_mode"] == DEFAULT_SERVING_MODE
    assert routed["items"][0]["shadow_score_logged"] == pytest.approx(0.77)


def test_security_auth_rbac_and_4h_incident_hold(tmp_path: Path):
    assert len(VALID_ROLES) == 5
    assert has_permission("SUPERVISOR", "workspace:block") is True
    assert has_permission("SALES_READER", "workspace:block") is False

    store = SessionStore(max_concurrent_sessions=MAX_CONCURRENT_SESSIONS)
    base_now = datetime(2026, 11, 22, 9, 0, tzinfo=VN_TZ)
    sessions = [store.create_session(f"sales_{i}", "SALES_READER", now=base_now) for i in range(5)]
    assert store.active_session_count(now=base_now) == 5

    # Phiên thứ 6 vượt giới hạn 5 phiên đồng thời
    with pytest.raises(HTTPException) as exc_429:
        store.create_session("sales_6", "SALES_READER", now=base_now)
    assert exc_429.value.status_code == 429

    # Kiểm tra xác thực qua token phiên (còn hạn -> OK, quá hạn TTL -> 401, thu hồi -> 401)
    ctx = resolve_auth_context(
        authorization=f"Bearer {sessions[0].session_id}",
        session_store=store,
        now=base_now + timedelta(hours=1),
    )
    assert ctx is not None and ctx.user_id == "sales_0" and ctx.role == "SALES_READER"

    with pytest.raises(HTTPException) as exc_expired:
        resolve_auth_context(
            authorization=f"Bearer {sessions[0].session_id}",
            session_store=store,
            now=base_now + timedelta(hours=9),
        )
    assert exc_expired.value.status_code == 401

    assert store.revoke_session(sessions[0].session_id) is True
    with pytest.raises(HTTPException) as exc_401:
        store.validate_session(sessions[0].session_id, now=base_now)
    assert exc_401.value.status_code == 401

    # Quy trình khóa sự cố 4 giờ (SEC-08)
    db_path = tmp_path / "sec.db"
    t0 = datetime(2026, 11, 22, 10, 0, tzinfo=VN_TZ)
    hold = activate_emergency_hold(
        db_path,
        reason="Nghi ngo ro ri PII trong file CSV",
        triggered_by="admin_01",
        severity="CRITICAL",
        now=t0,
    )
    assert hold["hold_code"] == HOLD_STALE_DATA
    assert get_active_incident_hold(db_path) is not None

    notif = record_purchaser_notification(
        db_path,
        hold["incident_id"],
        notified_by="admin_01",
        now=t0 + timedelta(hours=3, minutes=30),
    )
    assert notif["within_4h_sla"] is True

    released = release_emergency_hold(
        db_path,
        hold["incident_id"],
        released_by="admin_01",
        resolution_note="Da va loi va chay lai 40 control fixtures",
        now=t0 + timedelta(hours=5),
    )
    assert released["status"] == "RESOLVED"
    assert get_active_incident_hold(db_path) is None

    blk = commit_local_emergency_block(
        db_path,
        enquiry_id="SN-BLK-1",
        reason="Khach yeu cau ngung lien he",
        created_by="sup_01",
    )
    assert blk["blocked"] is True


def test_monitoring_drift_and_pilot_20bd_tracker(tmp_path: Path):
    # PLPT-58: Drift Monitor (OPS-06)
    df = pd.DataFrame(
        [
            {"enquiry_id": f"M-{i}", "product_family": "Motor_DongCo", "spec_completeness": "" if i < 2 else "Full", "source": "Website"}
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
    assert snap["auto_retrain_allowed"] is False
    alert_codes = {a["code"] for a in snap["alerts"]}
    assert "MISSINGNESS_SPIKE" in alert_codes
    assert "LOW_COVERAGE" in alert_codes

    # Chỉ đánh giá drift xếp hạng sau khi chín đủ 60 ngày
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
    drift = assess_mature_ranking_drift(enq_df, out_df, as_of_dt=datetime(2026, 11, 22, 10, 0, tzinfo=VN_TZ))
    assert drift["mature_eligible_count"] == 1
    assert drift["immature_excluded_count"] == 1
    assert drift["ranking_drift_assessable"] is True
    assert drift["auto_retrain_allowed"] is False

    # PLPT-57: Account-level assignment & 20 business days effort tracker
    assert assign_account_treatment("ACCT-001") == assign_account_treatment("acct-001")
    cohort_df = pd.DataFrame(
        [
            {"enquiry_id": "E1", "account_key": "ACCT-A"},
            {"enquiry_id": "E2", "account_key": "ACCT-A"},
            {"enquiry_id": "E3", "account_key": "ACCT-B"},
        ]
    )
    assigned_df, summary_assign = assign_cohort_by_account(cohort_df)
    assert summary_assign["conflicting_accounts"] == 0
    assert assigned_df.loc[assigned_df["account_key"] == "ACCT-A", "treatment_arm"].nunique() == 1

    pilot_db = tmp_path / "pilot.db"
    # Ghi nhận 20 ngày làm việc (bỏ T7/CN) với thời gian phân loại giảm từ 6 -> 3.5 phút (> 20%)
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

    pilot_summary = summarize_pilot_20bd(pilot_db, baseline_minutes_per_lead=6.0, baseline_on_time_rate=0.95)
    assert pilot_summary["min_20bd_met"] is True
    assert pilot_summary["effort_target_20pct_met"] is True
    assert pilot_summary["due_date_non_inferior"] is True


def test_ops_backup_restore_replay_and_portable_exit(tmp_path: Path):
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
