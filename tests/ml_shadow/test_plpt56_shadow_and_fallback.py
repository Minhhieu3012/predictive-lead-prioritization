"""Tests cho PLPT-56: Tách 1.000/200 theo thời gian, Shadow Scoring & Fallback tự động về Rules Baseline."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.features.pre_t0_store import LeakageError, build_pre_t0_features
from src.models.serving_router import (
    DEFAULT_SERVING_MODE,
    FALLBACK_SERVING_MODE,
    PREDICTIVE_SERVING_MODE,
    get_active_scoring_mode,
    resolve_serving_route,
    route_queue_scoring,
)
from src.models.shadow_runner import (
    SHADOW_SCORING_MODE,
    compute_shadow_rules_overlap,
    get_latest_batch_run,
    get_shadow_summary,
    log_batch_run,
    run_shadow_batch,
)
from src.models.train_candidates import (
    MIN_G0_NEG,
    MIN_G0_POS,
    MIN_G0_TOTAL,
    RefitForbiddenError,
    RegularizedLogisticRegressionModel,
    TabularPreprocessor,
    TemporalPartitionError,
    filter_mature_training_rows,
    get_registered_model,
    list_registered_models,
    partition_training_and_sealed_test,
    register_candidate_model,
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


def test_plpt63_partition_and_remediate_sprint3_debt():
    """PLPT-63: Khắc phục nợ kỹ thuật Sprint 3 - phân tách chuẩn 1.000 train / 200 sealed test."""
    train_start = datetime(2025, 1, 1, 8, 0, tzinfo=VN_TZ)
    test_start = datetime(2025, 9, 1, 8, 0, tzinfo=VN_TZ)
    cutoff_iso = "2025-08-31T23:59:59+07:00"

    enq_rows = []
    out_rows = []

    # 1.000 hồ sơ TRAINING_CANDIDATE
    for i in range(1000):
        t0 = train_start + timedelta(hours=i * 3)
        eid = f"TR-{i:04d}"
        enq_rows.append(
            make_cf_row(
                enquiry_id=eid,
                account_key=f"ACCT-{i % 300:04d}",
                t0_time=t0.isoformat(),
                cohort="TRAINING_CANDIDATE",
            )
        )
        out_rows.append({"enquiry_id": eid, "label": "POSITIVE" if i < 150 else "NEGATIVE"})

    # 200 hồ sơ SEALED_LATER_TEST
    for j in range(200):
        t0 = test_start + timedelta(hours=j * 6)
        eid = f"TE-{j:04d}"
        enq_rows.append(
            make_cf_row(
                enquiry_id=eid,
                account_key=f"ACCT-T-{j % 100:04d}",
                t0_time=t0.isoformat(),
                cohort="SEALED_LATER_TEST",
            )
        )
        out_rows.append({"enquiry_id": eid, "label": "POSITIVE" if j < 50 else "NEGATIVE"})

    enq_df = pd.DataFrame(enq_rows)
    out_df = pd.DataFrame(out_rows)

    parts = partition_training_and_sealed_test(
        enq_df,
        out_df,
        training_cutoff_t0=cutoff_iso,
        enforce_floors=True,
    )
    assert parts["train_count"] == 1000
    assert parts["train_pos"] >= 100
    assert parts["train_neg"] >= 100
    assert parts["sealed_count"] == 200
    assert parts["sealed_pos"] >= 40
    assert parts["sealed_neg"] >= 40

    # Khắc phục lỗi Train=1200: filter_mature_training_rows trên cả 1.200 dòng vẫn loại bỏ tuyệt đối SEALED_LATER_TEST
    train_only = filter_mature_training_rows(enq_df, out_df, training_cutoff_t0=cutoff_iso)
    assert len(train_only) == 1000
    assert "SEALED_LATER_TEST" not in train_only["cohort"].values

    # Ca vi phạm thể tích hoặc chồng lấn thời gian ném TemporalPartitionError
    with pytest.raises(TemporalPartitionError):
        partition_training_and_sealed_test(
            enq_df.iloc[:500],
            out_df.iloc[:500],
            training_cutoff_t0=cutoff_iso,
            enforce_floors=True,
        )


def test_plpt63_model_registry_and_prohibit_silent_refit(tmp_path: Path):
    """PLPT-63 & MOD 08: Đăng ký mô hình vào model_registry và cấm tự động refit cùng version."""
    db_path = tmp_path / "registry.db"
    manifest = {
        "model_version": "plpt-logreg-v1.0.0",
        "candidate_type": "REGULARIZED_LOGISTIC_REGRESSION",
        "feature_dictionary_hash": "sha256:abc12345",
        "preprocessor_hash": "sha256:prep9876",
        "random_seed": 42,
        "training_cutoff_t0": "2025-08-31T23:59:59+07:00",
        "training_row_count": 1000,
        "positive_count": 150,
        "negative_count": 850,
        "approval_status": "SHADOW_ONLY",
        "numerical_tolerance": 1e-6,
    }

    # Đăng ký lần đầu thành công
    res = register_candidate_model(db_path, manifest)
    assert res["model_version"] == "plpt-logreg-v1.0.0"
    assert res["approval_status"] == "SHADOW_ONLY"

    # Lấy thông tin mô hình đã đăng ký
    saved = get_registered_model(db_path, "plpt-logreg-v1.0.0")
    assert saved is not None
    assert saved["random_seed"] == 42
    assert saved["feature_dictionary_hash"] == "sha256:abc12345"

    models = list_registered_models(db_path)
    assert len(models) == 1

    # CẤM TỰ ĐỘNG REFIT: Gọi lại cùng version ném RefitForbiddenError
    with pytest.raises(RefitForbiddenError):
        register_candidate_model(db_path, manifest, allow_refit=False)


def test_plpt64_shadow_runner_sla_and_batch_run_logs(tmp_path: Path):
    """PLPT-64 / OPS 02: Shadow runner ghi vết vào shadow_scoring_logs và batch_run_logs với SLA <= 15 phút."""
    db_path = tmp_path / "shadow_sla.db"
    rows = [
        make_cf_row(enquiry_id=f"SH-{i:03d}", account_key=f"ACCT-{i % 10:02d}")
        for i in range(1, 21)
    ]
    create_clean_db(db_path, rows)

    feat_df, cols = build_pre_t0_features(pd.DataFrame(rows), strict_leakage=True)
    prep = TabularPreprocessor(cols).fit(feat_df)
    model = RegularizedLogisticRegressionModel().fit(
        prep.transform(feat_df),
        np.array([1.0 if i % 2 == 0 else 0.0 for i in range(len(rows))]),
    )

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_version": "plpt-logreg-v1.0.0",
                "approval_status": "SHADOW_ONLY",
                "serving_mode_locked": "RULES_BASELINE",
            }
        ),
        encoding="utf-8",
    )

    shadow_df = run_shadow_batch(db_path, manifest_path, model, prep, persist_logs=True)
    assert len(shadow_df) == 20
    assert "shadow_rank" in shadow_df.columns
    assert list(shadow_df["shadow_rank"]) == list(range(1, 21))

    # Kiểm tra bản ghi trong shadow_scoring_logs
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        count = conn.execute("SELECT COUNT(*) FROM shadow_scoring_logs").fetchone()[0]
        assert count == 20

    # Kiểm tra SLA và batch_run_logs
    latest_run = get_latest_batch_run(db_path)
    assert latest_run is not None
    assert latest_run["status"] == "SUCCESS"
    assert latest_run["records_processed"] == 20
    assert latest_run["sla_met"] == 1
    assert float(latest_run["duration_seconds"]) < 900.0

    # Kiểm tra tính năng Top-K overlap và shadow summary
    overlap = compute_shadow_rules_overlap(db_path, k=10, shadow_df=shadow_df)
    assert 0.0 <= overlap <= 1.0

    summary = get_shadow_summary(db_path, manifest_path=manifest_path, k=10)
    assert summary["active_serving_mode"] == "RULES_BASELINE"
    assert summary["sla_met"] is True
    assert summary["records_processed"] == 20
    assert summary["shadow_record_count"] == 20


def test_plpt56_manual_fallback_and_api_integration(tmp_path: Path):
    """PLPT-56 / Spec 09: Kích hoạt fallback thủ công và hiển thị rõ ràng trên API và hàng đợi."""
    from src.models.serving_router import (
        get_manual_fallback_state,
        reset_manual_fallback,
        trigger_manual_fallback,
    )
    from src.api.main import (
        get_shadow_summary_v1,
        post_shadow_fallback_v1,
        post_shadow_reset_v1,
        get_workspace_queue,
    )

    # Reset ban đầu
    reset_manual_fallback()
    assert get_manual_fallback_state()["manual_fallback_active"] is False

    # Kích hoạt fallback thủ công
    fb = trigger_manual_fallback("DRIFT_ALERT_TRIGGERED", triggered_by="TEST_REVIEWER")
    assert fb["status"] == "FALLBACK_ACTIVE"
    assert fb["fallback_reason"] == "DRIFT_ALERT_TRIGGERED"

    route = resolve_serving_route()
    assert route.active_mode == FALLBACK_SERVING_MODE
    assert route.fallback_triggered is True
    assert "DRIFT_ALERT_TRIGGERED" in str(route.fallback_reason)

    # Gọi API shadow fallback
    api_fb = post_shadow_fallback_v1({"reason": "AUDIT_FAIL"}, auth=None)
    assert api_fb["fallback_reason"] == "AUDIT_FAIL"

    # Gọi API shadow reset
    api_reset = post_shadow_reset_v1(_auth=None)
    assert api_reset["status"] == "RESET_SUCCESS"
    assert api_reset["fallback_triggered"] is False


def test_plpt65_shadow_summary_uses_gate_g1_decision_and_routes_to_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from src.api.main import app
    from src.models.serving_router import reset_manual_fallback

    reset_manual_fallback()
    db_path = tmp_path / "shadow_summary.db"
    create_clean_db(
        db_path,
        [make_cf_row(enquiry_id="SH-65-1", account_key="ACCT-SH-65")],
    )
    reference_path = tmp_path / "drift_reference.json"
    reference_path.write_text(
        json.dumps({"reference_missingness_pct": {}}),
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate_g1.json"
    gate_path.write_text(
        json.dumps(
            {
                "gate_g1_decision": "FALLBACK_RULES_BASELINE",
                "data_origin": "SYNTHETIC",
                "failed_criteria": ["REAL_DATA_VERIFIED"],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_version": "v1",
                "approval_status": "PENDING_GATE_G1_REVIEW",
                "serving_mode_locked": "RULES_BASELINE",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ACTIVE_SCORING_MODE", PREDICTIVE_SERVING_MODE)
    monkeypatch.setenv("DRIFT_REFERENCE_PATH", str(reference_path))
    monkeypatch.setenv("GATE_G1_REPORT_PATH", str(gate_path))
    monkeypatch.setenv("MODEL_MANIFEST_PATH", str(manifest_path))

    client = TestClient(app)
    headers = {"X-User-Role": "MODEL_REVIEWER"}
    summary = client.get("/shadow/summary", headers=headers)
    assert summary.status_code == 200
    route = summary.json()["route"]
    assert route["active_mode"] == FALLBACK_SERVING_MODE
    assert route["fallback_reason"].startswith("GATE_G1_FALLBACK_RULES_BASELINE_SYNTHETIC")
    assert "REAL_DATA_VERIFIED" in route["fallback_reason"]

    fallback = client.post("/shadow/fallback", headers=headers, json={})
    assert fallback.status_code == 200
    assert fallback.json()["active_mode"] == FALLBACK_SERVING_MODE
    assert "REAL_DATA_VERIFIED" in fallback.json()["fallback_reason"]


def test_plpt65_shadow_summary_routes_to_fallback_on_batch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from src.api.main import app
    from src.models.serving_router import reset_manual_fallback

    reset_manual_fallback()
    db_path = tmp_path / "shadow_batch_failure.db"
    create_clean_db(
        db_path,
        [make_cf_row(enquiry_id="SH-65-2", account_key="ACCT-SH-66")],
    )
    log_batch_run(
        db_path,
        batch_id="failed-shadow-batch",
        status="FAILED_PARTIAL",
        error_message="scoring model raised ValueError",
    )
    reference_path = tmp_path / "drift_reference.json"
    reference_path.write_text(
        json.dumps({"reference_missingness_pct": {}}),
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate_g1.json"
    gate_path.write_text(
        json.dumps(
            {"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"}
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_version": "v1",
                "approval_status": "APPROVED",
                "serving_mode_locked": "UNLOCKED",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ACTIVE_SCORING_MODE", PREDICTIVE_SERVING_MODE)
    monkeypatch.setenv("DRIFT_REFERENCE_PATH", str(reference_path))
    monkeypatch.setenv("GATE_G1_REPORT_PATH", str(gate_path))
    monkeypatch.setenv("MODEL_MANIFEST_PATH", str(manifest_path))

    response = TestClient(app).get(
        "/shadow/summary",
        headers={"X-User-Role": "MODEL_REVIEWER"},
    )
    assert response.status_code == 200
    route = response.json()["route"]
    assert route["active_mode"] == FALLBACK_SERVING_MODE
    assert route["fallback_reason"] == (
        "SHADOW_BATCH_FAILED: scoring model raised ValueError"
    )
    assert get_active_scoring_mode() == FALLBACK_SERVING_MODE


def test_plpt65_active_router_falls_back_when_drift_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from src.api.main import app
    from src.models.serving_router import reset_manual_fallback

    reset_manual_fallback()
    db_path = tmp_path / "shadow_drift.db"
    create_clean_db(
        db_path,
        [
            make_cf_row(
                enquiry_id="SH-65-3",
                account_key="ACCT-SH-67",
                spec_completeness="",
            )
        ],
    )
    reference_path = tmp_path / "drift_reference.json"
    reference_path.write_text(
        json.dumps({"reference_missingness_pct": {"spec_completeness": 0.0}}),
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate_g1.json"
    gate_path.write_text(
        json.dumps(
            {"gate_g1_decision": "APPROVED_PREDICTIVE_ML", "data_origin": "REAL"}
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_version": "v1",
                "approval_status": "APPROVED",
                "serving_mode_locked": "UNLOCKED",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("ACTIVE_SCORING_MODE", PREDICTIVE_SERVING_MODE)
    monkeypatch.setenv("DRIFT_REFERENCE_PATH", str(reference_path))
    monkeypatch.setenv("GATE_G1_REPORT_PATH", str(gate_path))
    monkeypatch.setenv("MODEL_MANIFEST_PATH", str(manifest_path))

    response = TestClient(app).get(
        "/shadow/summary",
        headers={"X-User-Role": "MODEL_REVIEWER"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["drift"]["recommended_fallback_to_rules"] is True
    assert payload["route"]["active_mode"] == FALLBACK_SERVING_MODE
    assert payload["route"]["fallback_reason"] == "DRIFT_THRESHOLD_BREACHED"
    assert get_active_scoring_mode() == FALLBACK_SERVING_MODE

