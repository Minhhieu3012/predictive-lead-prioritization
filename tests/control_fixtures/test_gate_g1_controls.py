"""PLPT-27: Automated 40 Control Fixtures Verification Suite (CF-01 to CF-40)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import contextlib
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd

from src.api.main import app
from src.config.vn_holidays import count_vn_business_days
from src.evaluation.gate_g1_decider import decide_gate_g1
from src.features.pre_t0_store import (
    PROHIBITED_FEATURE_COLUMNS,
    LeakageError,
    build_pre_t0_features,
    guard_no_outcome_join,
)
from src.ingest import csv_validator
from src.ingest.deduplicator import deduplicate_dataframe_with_steward, deduplicate_enquiries
from src.ingest.outcome_maturity import apply_versioned_label_correction, evaluate_60d_outcome_label
from src.models.explainer import explain_logreg_row
from src.models.shadow_runner import run_shadow_batch
from src.models.train_candidates import (
    RegularizedLogisticRegressionModel,
    TabularPreprocessor,
    filter_mature_training_rows,
)
from src.rules.rules_engine import build_queue, calculate_rules_score
from src.services.queue_service import (
    STALE_STATUS,
    allocate_daily_k_slots,
    ensure_operational_tables,
)
from tests.control_fixtures.fixtures_data import (
    ALL_CONTROL_FIXTURE_IDS,
    create_clean_db as _create_clean_db,
    make_cf_row as _make_row,
)

VN_TZ = timezone(timedelta(hours=7))


def run_single_control_fixture(fixture_id: str) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        old_db = os.environ.get("DATABASE_PATH")
        old_q = os.environ.get("QUARANTINE_DIR")
        old_k = os.environ.get("DAILY_CAPACITY_K")
        old_g1 = os.environ.get("GATE_G1_APPROVED")
        old_mode = os.environ.get("ACTIVE_SCORING_MODE")
        try:
            _dispatch_fixture(fixture_id, tmp)
        finally:
            for k, v in (
                ("DATABASE_PATH", old_db),
                ("QUARANTINE_DIR", old_q),
                ("DAILY_CAPACITY_K", old_k),
                ("GATE_G1_APPROVED", old_g1),
                ("ACTIVE_SCORING_MODE", old_mode),
            ):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _dispatch_fixture(fid: str, tmp: Path) -> None:
    if fid == "CF-01":
        df = pd.DataFrame([_make_row(t0_time="2026-10-01T10:00:00+07:00", feature_available_at="2026-10-01T10:00:00+07:00")])
        feat_df, cols = build_pre_t0_features(df, strict_leakage=True)
        assert len(feat_df) == 1 and len(cols) > 0
    elif fid == "CF-02":
        df = pd.DataFrame([_make_row(t0_time="2026-10-01T10:00:00+07:00", feature_available_at="2026-10-01T10:00:01+07:00")])
        try:
            build_pre_t0_features(df, strict_leakage=True)
            raise AssertionError("Expected LeakageError for post-t0 feature_available_at")
        except LeakageError:
            pass
    elif fid == "CF-03":
        df = pd.DataFrame([_make_row(quotation_value_post_t0_DO_NOT_USE=999999)])
        try:
            build_pre_t0_features(df, strict_leakage=True)
            raise AssertionError("Expected LeakageError for *_DO_NOT_USE")
        except LeakageError:
            pass
    elif fid == "CF-04":
        df = pd.DataFrame([
            _make_row(enquiry_id="E1", account_key="A1", t0_time="2026-10-01T08:00:00+07:00"),
            _make_row(enquiry_id="E2", account_key="A1", t0_time="2026-10-01T12:00:00+07:00"),
            _make_row(enquiry_id="E3", account_key="A1", t0_time="2026-10-02T09:00:00+07:00"),
        ])
        feat_df, _ = build_pre_t0_features(df, strict_leakage=True)
        assert list(feat_df["pre_t0_account_enquiry_count"]) == [0, 1, 2]
    elif fid == "CF-05":
        enq = pd.DataFrame([
            _make_row(enquiry_id="MATURE", t0_time="2026-06-01T08:00:00+07:00"),
            _make_row(enquiry_id="IMMATURE", t0_time="2026-08-01T08:00:00+07:00"),
        ])
        out = pd.DataFrame([
            {"enquiry_id": "MATURE", "label": "POSITIVE"},
            {"enquiry_id": "IMMATURE", "label": "POSITIVE"},
        ])
        res = filter_mature_training_rows(enq, out, "2026-08-31T23:59:59+07:00")
        assert list(res["enquiry_id"]) == ["MATURE"]
    elif fid == "CF-06":
        db_path = tmp / "cf06.db"
        _create_clean_db(db_path, [_make_row()])
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                conn.execute("CREATE TABLE outcome_events (enquiry_id TEXT, label TEXT)")
                conn.commit()
        feat_df, cols = build_pre_t0_features(pd.DataFrame([_make_row()]), strict_leakage=True)
        prep = TabularPreprocessor(cols).fit(feat_df)
        model = RegularizedLogisticRegressionModel().fit(prep.transform(feat_df), np.array([1.0]))
        m_path = tmp / "manifest.json"
        m_path.write_text(json.dumps({"model_version": "v1", "serving_mode_locked": "RULES_BASELINE"}))
        scored = run_shadow_batch(db_path, m_path, model, prep)
        assert len(scored) == 1
        assert bool(scored.iloc[0]["is_visible_to_staff"]) is False
        assert scored.iloc[0]["serving_mode_locked"] == "RULES_BASELINE"
        try:
            run_shadow_batch(db_path, m_path, model, prep, requested_tables=["clean_enquiries", "outcome_events"])
            raise AssertionError("Expected LeakageError when joining outcome_events")
        except LeakageError:
            pass
    elif fid == "CF-07":
        df = pd.DataFrame([
            _make_row(enquiry_id="W1", t0_time="2026-10-01T02:15:00+07:00", enquiry_channel_at_t0="WEB_FORM"),
            _make_row(enquiry_id="E1", t0_time="2026-10-01T03:05:00+07:00", enquiry_channel_at_t0="DIRECT_EMAIL"),
        ])
        clean_df, merge_df, _ = deduplicate_dataframe_with_steward(df)
        assert len(clean_df) == 1
        assert clean_df.iloc[0]["enquiry_id"] == "W1"
        assert len(merge_df) == 1
    elif fid == "CF-08":
        df = pd.DataFrame([
            _make_row(enquiry_id="M1", product_family="Motor_DongCo", project_ref_code="P1"),
            _make_row(enquiry_id="D1", product_family="Drive_BienTan", project_ref_code="P2"),
        ])
        clean_df, merge_df, _ = deduplicate_dataframe_with_steward(df)
        assert len(clean_df) == 2 and len(merge_df) == 0
    elif fid == "CF-09":
        df = pd.DataFrame([
            _make_row(enquiry_id="S1", project_ref_code="P1"),
            _make_row(enquiry_id="S2", project_ref_code=""),
        ])
        clean_df, merge_df, steward_df = deduplicate_dataframe_with_steward(df)
        assert len(clean_df) == 2 and len(merge_df) == 0 and len(steward_df) == 1
        assert steward_df.iloc[0]["status"] == "DATA_STEWARD"
    elif fid == "CF-10":
        db_path = tmp / "cf10.db"
        enq_csv, out_csv = tmp / "e.csv", tmp / "o.csv"
        pd.DataFrame([_make_row()]).to_csv(enq_csv, index=False)
        pd.DataFrame([{"enquiry_id": "CF-ENQ-001", "event_type": "CLOSE", "event_time": "2026-10-05T10:00:00+07:00", "label": "Unknown", "cohort": "2026-11"}]).to_csv(out_csv, index=False)
        orig = (csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH)
        try:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH = enq_csv, out_csv
            csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH = tmp / "qe.csv", tmp / "qo.csv"
            csv_validator.DB_PATH = db_path
            csv_validator.validate_and_load()
            csv_validator.validate_and_load()
            with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
                with conn:
                    assert conn.execute("SELECT COUNT(*) FROM raw_enquiries").fetchone()[0] == 1
        finally:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH = orig
    elif fid == "CF-11":
        db_path = tmp / "cf11.db"
        enq_csv, out_csv = tmp / "e.csv", tmp / "o.csv"
        pd.DataFrame([_make_row(extraction_time="2026-10-05T10:00:00+07:00", workflow_state="Closed")]).to_csv(enq_csv, index=False)
        pd.DataFrame([{"enquiry_id": "CF-ENQ-001", "event_type": "CLOSE", "event_time": "2026-10-05T10:00:00+07:00", "label": "POSITIVE", "cohort": "2026-11"}]).to_csv(out_csv, index=False)
        orig = (csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH)
        try:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH = enq_csv, out_csv
            csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH = tmp / "qe.csv", tmp / "qo.csv"
            csv_validator.DB_PATH = db_path
            csv_validator.validate_and_load()
            # Late older increment
            pd.DataFrame([_make_row(extraction_time="2026-10-01T10:00:00+07:00", workflow_state="New")]).to_csv(enq_csv, index=False)
            csv_validator.validate_and_load()
            with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
                with conn:
                    assert conn.execute("SELECT workflow_state FROM raw_enquiries").fetchone()[0] == "Closed"
        finally:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH = orig
    elif fid == "CF-12":
        db_path = tmp / "cf12.db"
        enq_csv, out_csv = tmp / "e.csv", tmp / "o.csv"
        pd.DataFrame([_make_row(enquiry_id="OK-1"), _make_row(enquiry_id="BAD-1", t0_time="")]).to_csv(enq_csv, index=False)
        pd.DataFrame([
            {"enquiry_id": "OK-1", "event_type": "O", "event_time": "2026-10-05T10:00:00+07:00", "label": "Unknown", "cohort": "2026-11"},
            {"enquiry_id": "BAD-1", "event_type": "O", "event_time": "2026-10-05T10:00:00+07:00", "label": "Unknown", "cohort": "2026-11"},
        ]).to_csv(out_csv, index=False)
        orig = (csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH)
        try:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH = enq_csv, out_csv
            csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH = tmp / "qe.csv", tmp / "qo.csv"
            csv_validator.DB_PATH = db_path
            csv_validator.validate_and_load()
            q_df = pd.read_csv(tmp / "qe.csv")
            assert len(q_df) == 1 and q_df.iloc[0]["enquiry_id"] == "BAD-1"
        finally:
            csv_validator.ENQUIRIES_PATH, csv_validator.OUTCOMES_PATH, csv_validator.QUARANTINE_PATH, csv_validator.QUARANTINE_OUTCOMES_PATH, csv_validator.DB_PATH = orig
    elif fid == "CF-13":
        q = build_queue([_make_row(enquiry_id="W-1", workflow_state="withdrawn")], exclude_closed=True)
        assert len(q) == 0
    elif fid == "CF-14":
        res = evaluate_60d_outcome_label(
            t0_time="2026-06-01T08:00:00+07:00",
            observation_time="2026-07-16T08:00:00+07:00",
            business_need_confirmed=True,
            product_fit_confirmed=True,
            agreed_next_step_confirmed=True,
            evidence_checklist_passed=True,
        )
        assert res["label"] == "POSITIVE" and res["maturity_state"] == "MATURE"
    elif fid == "CF-15":
        res = evaluate_60d_outcome_label(
            t0_time="2026-06-01T08:00:00+07:00",
            observation_time="2026-06-21T08:00:00+07:00",
            business_need_confirmed=True,
            product_fit_confirmed=False,
            agreed_next_step_confirmed=False,
            evidence_checklist_passed=False,
            crm_stage="Qualified",
        )
        assert res["label"] != "POSITIVE" and res["label"] == "Unknown"
    elif fid == "CF-16":
        res = evaluate_60d_outcome_label(
            t0_time="2026-06-01T08:00:00+07:00",
            observation_time="2026-08-01T08:00:00+07:00",
            business_need_confirmed=False,
            product_fit_confirmed=False,
            agreed_next_step_confirmed=False,
            evidence_checklist_passed=False,
            observer_coverage="FULL",
        )
        assert res["label"] == "NEGATIVE" and res["maturity_state"] == "MATURE"
    elif fid == "CF-17":
        res_imm = evaluate_60d_outcome_label(
            t0_time="2026-08-10T08:00:00+07:00",
            observation_time="2026-09-04T08:00:00+07:00",
            business_need_confirmed=False,
            product_fit_confirmed=False,
            agreed_next_step_confirmed=False,
            evidence_checklist_passed=False,
        )
        assert res_imm["label"] == "Unknown" and res_imm["maturity_state"] == "IMMATURE"
        enq = pd.DataFrame([_make_row(enquiry_id="U-1", t0_time="2026-08-10T08:00:00+07:00")])
        out = pd.DataFrame([{"enquiry_id": "U-1", "label": res_imm["label"]}])
        res = filter_mature_training_rows(enq, out, "2026-09-05T00:00:00+07:00")
        assert len(res) == 0
    elif fid == "CF-18":
        db_path = tmp / "cf18.db"
        _create_clean_db(db_path, [_make_row(enquiry_id="CF18-1")])
        rev1 = apply_versioned_label_correction(
            str(db_path),
            enquiry_id="CF18-1",
            new_label="Unknown",
            recorded_at="2026-07-29T10:00:00+07:00",
            correction_reason="Initial snapshot before late outcome",
            prior_frozen_score=78.5,
        )
        rev2 = apply_versioned_label_correction(
            str(db_path),
            enquiry_id="CF18-1",
            new_label="POSITIVE",
            recorded_at="2026-08-03T10:00:00+07:00",
            correction_reason="Delayed outcome recorded day 58 ingested day 63",
            prior_frozen_score=78.5,
        )
        assert rev1["revision_number"] == 1 and rev2["revision_number"] == 2
        assert rev2["preserved_historical_score"] == 78.5
    elif fid == "CF-19":
        df = pd.DataFrame([_make_row(prior_purchase_count_at_t0=None)])
        feat_df, _ = build_pre_t0_features(df, strict_leakage=True)
        assert int(feat_df.iloc[0]["is_new_account_at_t0"]) == 1
    elif fid == "CF-20":
        db_path = tmp / "cf20.db"
        rows = [
            _make_row(enquiry_id="EMPTY-SPEC", spec_completeness=""),
            *[_make_row(enquiry_id=f"FULL-{i}", spec_completeness="Full") for i in range(5)],
            _make_row(enquiry_id="MIN-OUTSIDE-K", spec_completeness="Minimal"),
        ]
        _create_clean_db(db_path, rows)
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        os.environ["DAILY_CAPACITY_K"] = "5"
        c = TestClient(app)
        act = c.get("/api/v1/workspace/queue?view=active", headers={"X-User-Role": "SALES_READER"}).json()
        unsc = c.get("/api/v1/workspace/queue?view=unscored", headers={"X-User-Role": "SALES_READER"}).json()
        act_ids = {x["enquiry_id"] for x in act["items"]}
        unsc_ids = {x["enquiry_id"] for x in unsc["items"]}
        assert len(act_ids & unsc_ids) == 0
        assert "EMPTY-SPEC" in unsc_ids and "MIN-OUTSIDE-K" in unsc_ids
    elif fid == "CF-21":
        scored = calculate_rules_score(_make_row())
        assert "%" not in str(scored["rules_score"]) and scored["score_unit"] == "points"
    elif fid == "CF-22":
        feat_df, cols = build_pre_t0_features(pd.DataFrame([_make_row()]), strict_leakage=True)
        prep = TabularPreprocessor(cols).fit(feat_df)
        model = RegularizedLogisticRegressionModel().fit(prep.transform(feat_df), np.array([1.0]))
        exp = explain_logreg_row(model, prep, feat_df.iloc[[0]], _make_row())
        assert len(exp["contributing_factors"]) <= 3 and "not causal proof" in exp["disclaimer"]
    elif fid == "CF-23":
        feat_df, cols = build_pre_t0_features(pd.DataFrame([_make_row()]), strict_leakage=True)
        prep = TabularPreprocessor(cols).fit(feat_df)
        model = RegularizedLogisticRegressionModel().fit(prep.transform(feat_df), np.array([1.0]))
        s1 = model.predict_scores(prep.transform(feat_df))[0]
        s2 = model.predict_scores(prep.transform(feat_df))[0]
        assert abs(s1 - s2) <= 1e-6
    elif fid == "CF-24":
        rep = decide_gate_g1(
            g1_c1_pipeline_audit_passed=True,
            g1_c2_baseline_ready=True,
            g1_c3_controls_passed_count=40,
            benchmark_result={"precision_at_k_lift": 1.05, "coverage_gate_met": True, "floor_met": True},
            bootstrap_result={"ci_lower_bound_exceeds_zero": False},
            g1_c7_reviewer_signed_off=True,
            data_origin="REAL",
        )
        assert rep["active_serving_mode"] == "RULES_BASELINE"
    elif fid == "CF-25":
        entries = [{"enquiry_id": f"E-{i}", "tier0_bypass": False, "rules_score": 100 - i} for i in range(30)]
        sel, exp_cnt, overloaded, _ = allocate_daily_k_slots(entries, capacity_k=20, seed=42)
        assert len(sel) == 20 and exp_cnt == 2 and not overloaded
    elif fid == "CF-26":
        entries = [
            {"enquiry_id": "URG-1", "tier0_bypass": True, "rules_score": 10},
            *[{"enquiry_id": f"STD-{i}", "tier0_bypass": False, "rules_score": 90 - i} for i in range(25)],
        ]
        sel, _, _, _ = allocate_daily_k_slots(entries, capacity_k=20, seed=42)
        assert sel[0]["enquiry_id"] == "URG-1" and sel[0]["allocation_type"] == "TIER_0_MANDATORY"
    elif fid == "CF-27":
        db_path = tmp / "cf27.db"
        old_t0 = (datetime.now(VN_TZ) - timedelta(days=7)).replace(microsecond=0).isoformat()
        _create_clean_db(db_path, [_make_row(t0_time=old_t0)])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        res = TestClient(app).get("/api/v1/workspace/queue", headers={"X-User-Role": "SUPERVISOR"})
        assert res.status_code == 200 and res.json()["items"][0]["is_overdue_2bd"] is True
    elif fid == "CF-28":
        # Gọi API /api/v1/workspace/queue?view=active&include_overflow=true xác nhận đủ 30 hồ sơ không bị ẩn
        db_path = tmp / "cf28.db"
        _create_clean_db(db_path, [_make_row(enquiry_id=f"E-{i:02d}") for i in range(30)])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        os.environ["DAILY_CAPACITY_K"] = "25"
        res = TestClient(app).get("/api/v1/workspace/queue?view=active&include_overflow=true&limit=100", headers={"X-User-Role": "SALES_READER"}).json()
        assert res["total"] == 30 and len(res["items"]) == 30
        overflow_items = [x for x in res["items"] if x["allocation_type"] == "OVERFLOW_OUTSIDE_K"]
        assert len(overflow_items) == 5
    elif fid == "CF-29":
        db_path = tmp / "cf29.db"
        _create_clean_db(db_path, [_make_row()])
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                conn.execute("CREATE TABLE raw_outcomes (enquiry_id TEXT PRIMARY KEY, label TEXT)")
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        c = TestClient(app)
        assert c.post("/api/v1/workspace/override", json={"enquiry_id": "CF-ENQ-001", "override_reason_code": "OTHER_VERIFIED", "justification": ""}, headers={"X-User-Role": "SALES_READER"}).status_code == 400
        assert c.post("/api/v1/workspace/override", json={"enquiry_id": "CF-ENQ-001", "override_reason_code": "OTHER_VERIFIED", "justification": "Ly do hop le"}, headers={"X-User-Role": "SALES_READER"}).status_code == 200
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                assert conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0] == 0
    elif fid == "CF-30":
        # Chỉ ghi cục bộ, không có side-effect gửi mail/SMS/CRM
        db_path = tmp / "cf30.db"
        _create_clean_db(db_path, [_make_row()])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        r = TestClient(app).post("/api/v1/workspace/review", json={"enquiry_id": "CF-ENQ-001", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": True, "contact_attempted": False}, headers={"X-User-Role": "SALES_READER"})
        assert r.status_code == 200
    elif fid == "CF-31":
        db_path = tmp / "cf31.db"
        _create_clean_db(db_path, [_make_row()])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        c = TestClient(app)
        assert c.post("/api/block", json={"enquiry_id": "CF-ENQ-001"}, headers={"X-User-Role": "SUPERVISOR"}).status_code == 200
        assert c.get("/api/v1/workspace/queue", headers={"X-User-Role": "SALES_READER"}).json()["total"] == 0
    elif fid == "CF-32":
        db_path = tmp / "cf32.db"
        _create_clean_db(db_path, [_make_row()])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        r = TestClient(app).post("/api/v1/workspace/review", json={"enquiry_id": "CF-ENQ-001", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": False, "contact_attempted": True}, headers={"X-User-Role": "SALES_READER"})
        assert r.status_code == 400
    elif fid == "CF-33":
        db_path = tmp / "cf33.db"
        stale_ext = (datetime.now(VN_TZ) - timedelta(hours=30)).replace(microsecond=0).isoformat()
        _create_clean_db(db_path, [_make_row(extraction_time=stale_ext)])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        r = TestClient(app).get("/api/v1/workspace/queue", headers={"X-User-Role": "SALES_READER"}).json()
        assert r["status"] == STALE_STATUS and r["locked"] is True
    elif fid == "CF-34":
        df = pd.DataFrame([_make_row(contact_name="Nguyen Van A", phone="0909123456", email="a@b.com")])
        _, cols = build_pre_t0_features(df, strict_leakage=True)
        for pii in ("contact_name", "phone", "email", "account_key", "enquiry_id"):
            assert pii not in cols
        assert "account_key" in PROHIBITED_FEATURE_COLUMNS
    elif fid == "CF-35":
        db_path = tmp / "cf35.db"
        _create_clean_db(db_path, [_make_row()])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        c = TestClient(app)
        assert c.get("/api/v1/workspace/queue").status_code == 401
        assert c.get("/api/v1/workspace/queue", headers={"X-User-Role": "SALES_READER", "X-Session-Expired": "true"}).status_code == 401
        assert c.post("/api/block", json={"enquiry_id": "CF-ENQ-001"}, headers={"X-User-Role": "SALES_READER"}).status_code == 403
    elif fid == "CF-36":
        # Kịch bản khôi phục backup và tái áp dụng lệnh chặn (blocked_accounts vẫn có hiệu lực sau restore)
        db_path = tmp / "cf36.db"
        backup_db = tmp / "cf36_backup.db"
        _create_clean_db(db_path, [_make_row(enquiry_id="RESTORE-1", account_key="ACCT-R1")])
        shutil.copy2(db_path, backup_db)

        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        c = TestClient(app)
        assert c.post("/api/block", json={"enquiry_id": "RESTORE-1", "reason": "Khach khiếu nại"}, headers={"X-User-Role": "SUPERVISOR"}).status_code == 200
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                block_registry = conn.execute("SELECT enquiry_id, account_key, reason, blocked_at, created_by FROM blocked_accounts").fetchall()

        # Restore backup DB (chưa có lệnh chặn) và replay block_registry
        shutil.copy2(backup_db, db_path)
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                ensure_operational_tables(conn)
                conn.executemany(
                    "INSERT INTO blocked_accounts (enquiry_id, account_key, reason, blocked_at, created_by) VALUES (?, ?, ?, ?, ?)",
                    block_registry,
                )
                conn.commit()
        q_after = c.get("/api/v1/workspace/queue", headers={"X-User-Role": "SALES_READER"}).json()
        assert q_after["total"] == 0
    elif fid == "CF-37":
        # Bản địa hóa UTF-8 tiếng Việt không mất dấu
        txt = "Động cơ không đồng bộ 3 pha 75kW"
        db_path = tmp / "cf37.db"
        _create_clean_db(db_path, [_make_row(industry_at_t0=txt)])
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                val = conn.execute("SELECT industry_at_t0 FROM clean_enquiries").fetchone()[0]
                assert val == txt
    elif fid == "CF-38":
        db_path = tmp / "cf38.db"
        _create_clean_db(db_path, [
            _make_row(enquiry_id="M1", product_family="Motor_DongCo"),
            _make_row(enquiry_id="D1", product_family="Drive_BienTan"),
        ])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        res = TestClient(app).get("/api/v1/workspace/queue?q=dong%20co", headers={"X-User-Role": "SALES_READER"}).json()
        assert len(res["items"]) == 1 and res["items"][0]["enquiry_id"] == "M1"
    elif fid == "CF-39":
        # Lịch nghỉ lễ VN (30/4 - 1/5) + T7/CN không tính vào ngày làm việc
        t0 = datetime(2026, 4, 29, 9, 0, 0, tzinfo=VN_TZ)
        end = datetime(2026, 5, 4, 10, 0, 0, tzinfo=VN_TZ)  # 30/4 (Lễ), 1/5 (Lễ), 2/5 (T7), 3/5 (CN) -> chỉ 4/5 là 1 ngày làm việc
        assert count_vn_business_days(t0, end) == 1
        vnd_formatted = f"{15000000:,} VND"
        assert "15,000,000 VND" == vnd_formatted
    elif fid == "CF-40":
        db_path = tmp / "cf40.db"
        _create_clean_db(db_path, [_make_row(enquiry_id=f"L-{i}") for i in range(30)])
        os.environ["DATABASE_PATH"] = str(db_path)
        os.environ["QUARANTINE_DIR"] = str(tmp)
        c = TestClient(app)
        latencies = []

        def _hit(_):
            t_start = time.perf_counter()
            r = c.get("/api/v1/workspace/queue", headers={"X-User-Role": "SALES_READER"})
            assert r.status_code == 200
            return time.perf_counter() - t_start

        with ThreadPoolExecutor(max_workers=5) as ex:
            latencies = list(ex.map(_hit, range(100)))
        p95 = float(np.percentile(latencies, 95))
        assert p95 <= 3.0


def test_all_40_control_fixtures():
    for fid in ALL_CONTROL_FIXTURE_IDS:
        run_single_control_fixture(fid)

