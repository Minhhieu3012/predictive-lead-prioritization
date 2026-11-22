"""Tests cho PLPT-26 & PLPT-28: Pre-t0 Feature Store, Candidate ML Training, Explainer & Gate G1 Decider."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

from src.evaluation.gate_g1_decider import decide_gate_g1
from src.evaluation.paired_bootstrap import run_paired_cluster_bootstrap
from src.evaluation.temporal_benchmark import evaluate_temporal_cohort
from src.features.pre_t0_store import (
    MAX_FEATURE_COLUMNS,
    LeakageError,
    build_pre_t0_features,
    guard_no_outcome_join,
)
from src.models.explainer import explain_logreg_row, explain_tree_row
from src.models.train_candidates import GateG0NotMetError, train_candidate_models

VN_TZ = timezone(timedelta(hours=7))


def _make_synthetic_dataset(n_train: int = 240, n_test: int = 220):
    rng = np.random.default_rng(42)
    base_train = datetime(2026, 4, 1, 8, 0, 0, tzinfo=VN_TZ)
    base_test = datetime(2026, 9, 5, 8, 0, 0, tzinfo=VN_TZ)

    enq_rows = []
    out_rows = []

    for i in range(n_train + n_test):
        is_train = i < n_train
        t0 = (base_train + timedelta(days=i % 45)) if is_train else (base_test + timedelta(days=i % 20))
        spec = "Full" if (i % 3 == 0) else ("Partial" if i % 3 == 1 else "Minimal")
        prior = None if (i % 5 == 0) else int(i % 4 + 1)
        pos_prob = 0.65 if spec == "Full" else (0.25 if spec == "Partial" else 0.10)
        label = "POSITIVE" if rng.random() < pos_prob else "NEGATIVE"

        enq_id = f"SYN-{i:04d}"
        enq_rows.append(
            {
                "enquiry_id": enq_id,
                "account_key": f"ACCT-{i % 80:03d}",
                "t0_time": t0.isoformat(),
                "extraction_time": (t0 + timedelta(hours=1)).isoformat(),
                "product_family": ["Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"][i % 3],
                "spec_completeness": spec,
                "is_urgent_replacement": "Urgent" if i % 15 == 0 else "Normal",
                "contract_due_date": (t0 + timedelta(days=20)).date().isoformat(),
                "industry_at_t0": "Manufacturing",
                "enquiry_channel_at_t0": "WEB_FORM" if i % 2 == 0 else "DIRECT_EMAIL",
                "region_at_t0": "South",
                "account_age_days_at_t0": 365,
                "prior_purchase_count_at_t0": prior,
                "estimated_value_band_at_t0": "HIGH" if spec == "Full" else "LOW",
                "feature_event_time": t0.isoformat(),
                "feature_available_at": t0.isoformat(),
            }
        )
        out_rows.append(
            {
                "enquiry_id": enq_id,
                "label": label,
            }
        )

    return pd.DataFrame(enq_rows), pd.DataFrame(out_rows)


def test_pre_t0_store_leakage_and_pii_guards():
    df = pd.DataFrame([
        {
            "enquiry_id": "E-1",
            "account_key": "A-1",
            "t0_time": "2026-10-01T10:00:00+07:00",
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "feature_event_time": "2026-10-01T10:00:00+07:00",
            "feature_available_at": "2026-10-01T10:00:00+07:00",
        },
        {
            "enquiry_id": "E-2",
            "account_key": "A-1",
            "t0_time": "2026-10-02T10:00:00+07:00",
            "product_family": "Motor_DongCo",
            "spec_completeness": "Minimal",
            "feature_event_time": "2026-10-02T10:00:00+07:00",
            "feature_available_at": "2026-10-02T10:00:00+07:00",
        },
    ])
    feat_df, feat_cols = build_pre_t0_features(df, strict_leakage=True)
    assert len(feat_cols) <= MAX_FEATURE_COLUMNS
    assert "account_key" not in feat_cols
    assert "enquiry_id" not in feat_cols
    assert list(feat_df["pre_t0_account_enquiry_count"]) == [0, 1]

    # CF-02: post-t0 feature_available_at > t0_time bị chặn
    leak_df = df.copy()
    leak_df.loc[0, "feature_available_at"] = "2026-10-01T10:00:01+07:00"
    try:
        build_pre_t0_features(leak_df, strict_leakage=True)
        assert False, "Expected LeakageError"
    except LeakageError:
        pass

    # CF-06: outcome join guard
    try:
        guard_no_outcome_join(["product_family", "label"])
        assert False, "Expected LeakageError"
    except LeakageError:
        pass


def test_ml_training_explainer_and_gate_g1_pipeline(tmp_path: Path):
    enq_df, out_df = _make_synthetic_dataset(n_train=240, n_test=220)
    cutoff = "2026-08-31T23:59:59+07:00"

    # Gate G0 strict chặn khi < 1,000 mẫu
    try:
        train_candidate_models(enq_df, out_df, training_cutoff_t0=cutoff, enforce_g0_gate=True)
        assert False, "Expected GateG0NotMetError"
    except GateG0NotMetError:
        pass

    # Chạy pipeline kiểm thử trên dữ liệu giả lập với enforce_g0_gate=False
    trained = train_candidate_models(
        enq_df,
        out_df,
        training_cutoff_t0=cutoff,
        artifact_dir=tmp_path / "models",
        enforce_g0_gate=False,
        data_origin="SYNTHETIC",
    )
    manifest = trained["manifest"]
    assert manifest["serving_mode_locked"] == "RULES_BASELINE"
    assert (tmp_path / "models" / manifest["model_version"] / "manifest.json").exists()

    # Explainer top-3
    sealed_raw = enq_df.merge(out_df, on="enquiry_id")
    sealed_feat, _ = build_pre_t0_features(sealed_raw, strict_leakage=True)
    exp_lr = explain_logreg_row(trained["logreg"], trained["preprocessor"], sealed_feat.iloc[[0]], sealed_raw.iloc[0].to_dict())
    exp_dt = explain_tree_row(trained["dtree"], trained["preprocessor"], sealed_feat.iloc[[0]], sealed_raw.iloc[0].to_dict())
    assert len(exp_lr["contributing_factors"]) == 3
    assert len(exp_dt["contributing_factors"]) <= 3
    assert "not causal proof" in exp_lr["disclaimer"]

    # Temporal benchmark + paired bootstrap + Gate G1 decider
    x_sealed = trained["preprocessor"].transform(sealed_feat)
    sealed_raw["model_score"] = trained["logreg"].predict_scores(x_sealed)
    sealed_raw["baseline_score"] = 50.0

    bench = evaluate_temporal_cohort(
        sealed_raw,
        training_cutoff_t0=cutoff,
        capacity_k=10,
    )
    assert bench["floor_met"] is True
    assert bench["coverage_gate_met"] is True

    boot = run_paired_cluster_bootstrap(
        bench["sealed_df"],
        set(bench["baseline_selected_df"]["enquiry_id"]),
        set(bench["model_selected_df"]["enquiry_id"]),
        n_bootstrap=200,
        random_state=42,
    )
    assert len(boot["paired_95_ci"]) == 2

    # Trên dữ liệu SYNTHETIC bắt buộc FALLBACK_RULES_BASELINE
    g1_syn = decide_gate_g1(
        g1_c1_pipeline_audit_passed=True,
        g1_c2_baseline_ready=True,
        g1_c3_controls_passed_count=40,
        benchmark_result=bench,
        bootstrap_result=boot,
        g1_c7_reviewer_signed_off=True,
        data_origin="SYNTHETIC",
        report_path=tmp_path / "gate_g1_report.json",
    )
    assert g1_syn["gate_g1_decision"] == "FALLBACK_RULES_BASELINE"
    assert g1_syn["active_serving_mode"] == "RULES_BASELINE"
    assert "REAL_DATA_VERIFIED" in g1_syn["failed_criteria"]
    assert (tmp_path / "gate_g1_report.json").exists()

    # Mô phỏng dữ liệu REAL đạt đủ G1-C1..G1-C7 -> APPROVED_PREDICTIVE_ML
    fake_bench = {
        "capacity_k": 20,
        "cohort_counts": bench["cohort_counts"],
        "floor_met": True,
        "coverage_gate_met": True,
        "rules_baseline_metrics": bench["rules_baseline_metrics"],
        "candidate_model_metrics": bench["candidate_model_metrics"],
        "precision_at_k_lift": 1.29,
    }
    fake_boot = {"paired_diff_mean": 0.08, "paired_95_ci": [0.02, 0.15], "ci_lower_bound_exceeds_zero": True}
    g1_real = decide_gate_g1(
        g1_c1_pipeline_audit_passed=True,
        g1_c2_baseline_ready=True,
        g1_c3_controls_passed_count=40,
        benchmark_result=fake_bench,
        bootstrap_result=fake_boot,
        g1_c7_reviewer_signed_off=True,
        data_origin="REAL",
    )
    assert g1_real["gate_g1_decision"] == "APPROVED_PREDICTIVE_ML"
    assert g1_real["active_serving_mode"] == "PREDICTIVE_ML"


def test_gate_g1_failure_modes_and_single_use_lock(tmp_path: Path):
    from src.evaluation.temporal_benchmark import SealedCohortAlreadyUsedError

    enq_df, out_df = _make_synthetic_dataset(n_train=240, n_test=220)
    cutoff = "2026-08-31T23:59:59+07:00"
    trained = train_candidate_models(enq_df, out_df, training_cutoff_t0=cutoff, enforce_g0_gate=False)
    merged = enq_df.merge(out_df, on="enquiry_id")
    feat_df, _ = build_pre_t0_features(merged, strict_leakage=True)
    merged["model_score"] = trained["logreg"].predict_scores(trained["preprocessor"].transform(feat_df))
    merged["baseline_score"] = 50.0

    lock_file = tmp_path / "locks.json"
    bench = evaluate_temporal_cohort(merged, training_cutoff_t0=cutoff, capacity_k=10, lock_file=lock_file)

    # Khóa single-use: chạy lại cùng tập sealed cohort phải ném SealedCohortAlreadyUsedError
    try:
        evaluate_temporal_cohort(merged, training_cutoff_t0=cutoff, capacity_k=10, lock_file=lock_file)
        assert False, "Expected SealedCohortAlreadyUsedError"
    except SealedCohortAlreadyUsedError:
        pass

    # Ca thiếu dữ liệu / coverage thấp / lift < 1.20 / CI <= 0 / thiếu phê duyệt đều phải fallback
    for overrides in (
        {"floor_met": False},
        {"coverage_gate_met": False},
        {"precision_at_k_lift": 1.15},
    ):
        b = {**bench, **overrides}
        if "precision_at_k_lift" not in overrides:
            b["precision_at_k_lift"] = 1.25
        rep = decide_gate_g1(
            g1_c1_pipeline_audit_passed=True,
            g1_c2_baseline_ready=True,
            g1_c3_controls_passed_count=40,
            benchmark_result=b,
            bootstrap_result={"paired_diff_mean": 0.05, "paired_95_ci": [0.01, 0.09], "ci_lower_bound_exceeds_zero": True},
            g1_c7_reviewer_signed_off=True,
            data_origin="REAL",
        )
        assert rep["gate_g1_decision"] == "FALLBACK_RULES_BASELINE"

    # CI lower bound <= 0 -> fallback
    rep_ci = decide_gate_g1(
        g1_c1_pipeline_audit_passed=True,
        g1_c2_baseline_ready=True,
        g1_c3_controls_passed_count=40,
        benchmark_result={**bench, "precision_at_k_lift": 1.25},
        bootstrap_result={"paired_diff_mean": 0.02, "paired_95_ci": [-0.01, 0.05], "ci_lower_bound_exceeds_zero": False},
        g1_c7_reviewer_signed_off=True,
        data_origin="REAL",
    )
    assert rep_ci["gate_g1_decision"] == "FALLBACK_RULES_BASELINE"

    # Thiếu phê duyệt Model Reviewer (G1-C7=False) -> fallback
    rep_rev = decide_gate_g1(
        g1_c1_pipeline_audit_passed=True,
        g1_c2_baseline_ready=True,
        g1_c3_controls_passed_count=40,
        benchmark_result={**bench, "precision_at_k_lift": 1.25},
        bootstrap_result={"paired_diff_mean": 0.05, "paired_95_ci": [0.01, 0.09], "ci_lower_bound_exceeds_zero": True},
        g1_c7_reviewer_signed_off=False,
        data_origin="REAL",
    )
    assert rep_rev["gate_g1_decision"] == "FALLBACK_RULES_BASELINE"


def test_paired_bootstrap_insufficient_clusters():
    # 3 clusters (< min_clusters=10): model chọn toàn POSITIVE, baseline chọn toàn NEGATIVE
    sealed_df = pd.DataFrame([
        {"enquiry_id": "C1-M", "account_key": "A1", "t0_time": "2026-09-01T08:00:00+07:00", "label": "POSITIVE"},
        {"enquiry_id": "C1-B", "account_key": "A1", "t0_time": "2026-09-01T08:00:00+07:00", "label": "NEGATIVE"},
        {"enquiry_id": "C2-M", "account_key": "A2", "t0_time": "2026-09-02T08:00:00+07:00", "label": "POSITIVE"},
        {"enquiry_id": "C2-B", "account_key": "A2", "t0_time": "2026-09-02T08:00:00+07:00", "label": "NEGATIVE"},
        {"enquiry_id": "C3-M", "account_key": "A3", "t0_time": "2026-09-03T08:00:00+07:00", "label": "POSITIVE"},
        {"enquiry_id": "C3-B", "account_key": "A3", "t0_time": "2026-09-03T08:00:00+07:00", "label": "NEGATIVE"},
    ])
    res_low = run_paired_cluster_bootstrap(
        sealed_df,
        baseline_selected_ids={"C1-B", "C2-B", "C3-B"},
        model_selected_ids={"C1-M", "C2-M", "C3-M"},
        n_bootstrap=200,
        random_state=42,
        min_clusters=10,
    )
    assert res_low["n_clusters"] == 3
    assert res_low["sufficient_clusters"] is False
    assert res_low["ci_lower_bound_exceeds_zero"] is False

    res_ok = run_paired_cluster_bootstrap(
        sealed_df,
        baseline_selected_ids={"C1-B", "C2-B", "C3-B"},
        model_selected_ids={"C1-M", "C2-M", "C3-M"},
        n_bootstrap=200,
        random_state=42,
        min_clusters=3,
    )
    assert res_ok["n_clusters"] == 3
    assert res_ok["sufficient_clusters"] is True
    assert res_ok["ci_lower_bound_exceeds_zero"] is True


def test_pre_t0_store_lineage_and_account_masking(tmp_path: Path):
    import contextlib
    import sqlite3
    from src.features.pre_t0_store import build_pre_t0_store

    db_path = tmp_path / "feat_store.db"
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                """
                CREATE TABLE clean_enquiries (
                    enquiry_id TEXT PRIMARY KEY,
                    account_key TEXT,
                    t0_time TEXT,
                    product_family TEXT,
                    spec_completeness TEXT,
                    is_urgent_replacement TEXT,
                    contract_due_date TEXT,
                    industry_at_t0 TEXT,
                    enquiry_channel_at_t0 TEXT,
                    region_at_t0 TEXT,
                    account_age_days_at_t0 INTEGER,
                    prior_purchase_count_at_t0 INTEGER,
                    estimated_value_band_at_t0 TEXT,
                    feature_event_time TEXT,
                    feature_available_at TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO clean_enquiries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "E-OK-1", "ACCT-1", "2026-10-01T08:00:00+07:00",
                        "Motor_DongCo", "Full", "Normal", "2026-10-20",
                        "Manufacturing", "WEB_FORM", "South", 100, 1, "HIGH",
                        "2026-10-01T08:00:00+07:00", "2026-10-01T08:00:00+07:00",
                    ),
                    (
                        "E-OK-2", "ACCT-1", "2026-10-05T08:00:00+07:00",
                        "Drive_BienTan", "Partial", "Normal", "2026-10-25",
                        "Manufacturing", "DIRECT_EMAIL", "South", 104, 1, "MEDIUM",
                        "2026-10-05T08:00:00+07:00", "2026-10-05T08:00:00+07:00",
                    ),
                    (
                        "E-NO-ACCT", "", "2026-10-06T08:00:00+07:00",
                        "Motor_DongCo", "Full", "Normal", "2026-10-26",
                        "Manufacturing", "WEB_FORM", "North", None, None, "LOW",
                        "2026-10-06T08:00:00+07:00", "2026-10-06T08:00:00+07:00",
                    ),
                    (
                        "E-LEAK", "ACCT-9", "2026-10-01T08:00:00+07:00",
                        "Motor_DongCo", "Full", "Normal", "2026-10-20",
                        "Manufacturing", "WEB_FORM", "South", 50, 0, "HIGH",
                        "2026-10-01T09:00:00+07:00", "2026-10-01T08:00:00+07:00",
                    ),
                ],
            )
            conn.commit()

    res = build_pre_t0_store(db_path)
    assert res["status"] == "SUCCESS"
    assert res["feature_count"] == 3
    assert res["rejected_count"] == 1

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.row_factory = sqlite3.Row
            feat_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pre_t0_features)").fetchall()}
            lin_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pre_t0_feature_lineage)").fetchall()}
            assert "account_key" not in feat_cols
            assert "account_key" not in lin_cols

            feats = {r["enquiry_id"]: dict(r) for r in conn.execute("SELECT * FROM pre_t0_features").fetchall()}
            assert feats["E-OK-1"]["account_history_status"] == "NO_PRIOR_ENQUIRY_OBSERVED"
            assert feats["E-OK-2"]["account_history_status"] == "HISTORY_FOUND"
            assert feats["E-NO-ACCT"]["account_history_status"] == "UNKNOWN"
            assert "E-LEAK" not in feats

            lin = {r["enquiry_id"]: dict(r) for r in conn.execute("SELECT * FROM pre_t0_feature_lineage").fetchall()}
            assert lin["E-LEAK"]["feature_status"] == "REJECTED"
            assert lin["E-LEAK"]["rejection_code"] == "FEATURE_EVENT_AFTER_T0"
            assert lin["E-OK-1"]["feature_status"] == "READY"
            assert lin["E-OK-1"]["rejection_code"] is None
