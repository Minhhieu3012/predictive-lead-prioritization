"""Tests cho PLPT-56: Tách 1.000/200 theo thời gian, Shadow Scoring & Fallback tự động về Rules Baseline."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

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
from src.models.train_candidates import (
    MIN_G0_NEG,
    MIN_G0_POS,
    MIN_G0_TOTAL,
    RegularizedLogisticRegressionModel,
    TabularPreprocessor,
    filter_mature_training_rows,
)
from tests.control_fixtures.fixtures_data import create_clean_db, make_cf_row

VN_TZ = timezone(timedelta(hours=7))


def test_plpt56_temporal_1000_train_200_sealed_test_split():
    """Xác minh phân tách lát cắt thời gian 1.000 train (>=100P/100N) & 200 test (>=40P/40N) không chồng lấn."""
    train_start = datetime(2025, 1, 1, 8, 0, tzinfo=VN_TZ)
    test_start = datetime(2025, 9, 1, 8, 0, tzinfo=VN_TZ)
    cutoff_iso = "2025-08-31T23:59:59+07:00"

    enq_rows = []
    out_rows = []

    for i in range(1000):
        t0 = train_start + timedelta(hours=i * 3)
        eid = f"TR-{i:04d}"
        enq_rows.append(
            make_cf_row(
                enquiry_id=eid,
                account_key=f"ACCT-{i % 400:04d}",
                t0_time=t0.isoformat(),
                feature_event_time=t0.isoformat(),
                feature_available_at=t0.isoformat(),
            )
        )
        out_rows.append({"enquiry_id": eid, "label": "POSITIVE" if i < 150 else "NEGATIVE"})

    for j in range(200):
        t0 = test_start + timedelta(hours=j * 6)
        eid = f"TE-{j:04d}"
        enq_rows.append(
            make_cf_row(
                enquiry_id=eid,
                account_key=f"ACCT-T-{j % 120:04d}",
                t0_time=t0.isoformat(),
                feature_event_time=t0.isoformat(),
                feature_available_at=t0.isoformat(),
            )
        )
        out_rows.append({"enquiry_id": eid, "label": "POSITIVE" if j < 50 else "NEGATIVE"})

    enq_df = pd.DataFrame(enq_rows)
    out_df = pd.DataFrame(out_rows)

    mature_train = filter_mature_training_rows(enq_df, out_df, training_cutoff_t0=cutoff_iso)
    assert len(mature_train) >= MIN_G0_TOTAL
    assert int((mature_train["label"] == "POSITIVE").sum()) >= MIN_G0_POS
    assert int((mature_train["label"] == "NEGATIVE").sum()) >= MIN_G0_NEG

    cutoff_ts = pd.to_datetime(cutoff_iso, utc=True)
    test_mask = pd.to_datetime(enq_df["t0_time"], utc=True) > cutoff_ts
    sealed_test = enq_df[test_mask].merge(out_df, on="enquiry_id")
    assert len(sealed_test) == 200
    assert int((sealed_test["label"] == "POSITIVE").sum()) >= 40
    assert int((sealed_test["label"] == "NEGATIVE").sum()) >= 40

    train_t0_max = pd.to_datetime(mature_train["t0_time"], utc=True).max()
    test_t0_min = pd.to_datetime(sealed_test["t0_time"], utc=True).min()
    assert train_t0_max < test_t0_min


def test_plpt56_shadow_runner_isolation_and_router_auto_fallback(tmp_path: Path):
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
        assert conn.execute("SELECT COUNT(*) FROM shadow_score_logs").fetchone()[0] == 1

    with pytest.raises(LeakageError):
        run_shadow_batch(
            db_path,
            manifest_path,
            model,
            prep,
            requested_tables=["clean_enquiries", "raw_outcomes"],
        )

    # Mặc định trả về RULES_BASELINE
    default_route = resolve_serving_route(manifest=manifest_path)
    assert default_route.active_mode == DEFAULT_SERVING_MODE
    assert default_route.rules_only_enforced is True
    assert default_route.fallback_triggered is False

    # Gate G1 chưa đạt / SYNTHETIC -> fallback tự động
    fb_syn = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "FALLBACK_RULES_BASELINE", "data_origin": "SYNTHETIC"},
        manifest=manifest_path,
    )
    assert fb_syn.active_mode == FALLBACK_SERVING_MODE
    assert fb_syn.fallback_triggered is True

    # Manifest chưa mở khóa / chưa APPROVED -> fallback tự động
    fb_manifest = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
        manifest=manifest_path,
    )
    assert fb_manifest.active_mode == FALLBACK_SERVING_MODE
    assert fb_manifest.fallback_triggered is True

    # Đủ điều kiện REAL + APPROVED + healthy -> mở PREDICTIVE_ML
    approved = resolve_serving_route(
        requested_mode=PREDICTIVE_SERVING_MODE,
        gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
        manifest={"model_version": "v2", "approval_status": "APPROVED", "serving_mode_locked": "UNLOCKED"},
    )
    assert approved.active_mode == PREDICTIVE_SERVING_MODE
    assert approved.fallback_triggered is False

    # Drift / Control failure / Incident hold -> fallback tức thì
    for kwargs, expected_reason in (
        ({"drift_fallback_triggered": True}, "DRIFT_THRESHOLD_BREACHED"),
        ({"control_health_ok": False}, "CONTROL_CHECK_FAILED"),
        ({"incident_hold_active": True}, "INCIDENT_HOLD_ACTIVE"),
    ):
        fb = resolve_serving_route(
            requested_mode=PREDICTIVE_SERVING_MODE,
            gate_g1_report={"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"},
            manifest={"model_version": "v2", "approval_status": "APPROVED", "serving_mode_locked": "UNLOCKED"},
            **kwargs,
        )
        assert fb.active_mode == FALLBACK_SERVING_MODE
        assert fb.fallback_reason == expected_reason

    routed = route_queue_scoring([row], route_decision=default_route, shadow_scores_by_id={"SH-001": 0.77})
    assert routed["route"]["active_mode"] == DEFAULT_SERVING_MODE
    assert routed["items"][0]["shadow_score_logged"] == pytest.approx(0.77)



def test_plpt66_http_shadow_summary_fallback_and_failed_partial_guard(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.models.shadow_runner import run_shadow_batch_with_audit
    from src.models.train_candidates import register_model_in_registry
    from src.security.auth import DEFAULT_SESSION_STORE

    db_path = tmp_path / "plpt66.db"
    row = make_cf_row(enquiry_id="SH-66", account_key="ACCT-66")
    create_clean_db(db_path, [row])
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "q"))
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    DEFAULT_SESSION_STORE._sessions.clear()

    # Registry & FAILED_PARTIAL guard
    reg = register_model_in_registry(
        db_path,
        {
            "model_version": "plpt-logreg-v1.0.0",
            "candidate_type": "REGULARIZED_LOGISTIC_REGRESSION",
            "random_seed": 42,
            "training_cutoff_t0": "2026-08-31T23:59:59+07:00",
            "training_row_count": 1000,
            "feature_dictionary_hash": "sha256:abc",
            "preprocessor_hash": "sha256:def",
            "data_origin": "SYNTHETIC",
        },
        sealed_test_count=200,
    )
    assert reg["auto_retrain_allowed"] is False

    feat_df, cols = build_pre_t0_features(pd.DataFrame([row]), strict_leakage=True)
    prep = TabularPreprocessor(cols).fit(feat_df)
    model = RegularizedLogisticRegressionModel().fit(prep.transform(feat_df), np.array([1.0]))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"model_version": "plpt-logreg-v1.0.0", "serving_mode_locked": "RULES_BASELINE"}), encoding="utf-8")

    fail_res = run_shadow_batch_with_audit(db_path, manifest_path, model, prep, simulate_partial_failure=True)
    assert fail_res["status"] == "FAILED_PARTIAL" and fail_res["scores_persisted"] is False

    ok_res = run_shadow_batch_with_audit(db_path, manifest_path, model, prep, simulate_partial_failure=False)
    assert ok_res["status"] == "SUCCESS" and ok_res["scores_persisted"] is True

    # HTTP /shadow/summary & /shadow/fallback RBAC
    client = TestClient(app)
    sales_tok = client.post("/auth/login", json={"user_id": "sales_66", "role": "SALES_READER"}).json()["token"]
    rev_tok = client.post("/auth/login", json={"user_id": "rev_66", "role": "MODEL_REVIEWER"}).json()["token"]

    assert client.get("/shadow/summary", headers={"Authorization": f"Bearer {sales_tok}"}).status_code == 403
    r_sum = client.get("/shadow/summary", headers={"Authorization": f"Bearer {rev_tok}"})
    assert r_sum.status_code == 200 and r_sum.json()["shadow_score_logs_count"] == 1
    r_fb = client.post("/shadow/fallback", json={"drift_fallback_triggered": True}, headers={"Authorization": f"Bearer {rev_tok}"})
    assert r_fb.status_code == 200 and r_fb.json()["active_mode"] == FALLBACK_SERVING_MODE
