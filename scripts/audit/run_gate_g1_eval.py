"""PLPT-28: Runner đánh giá Temporal Benchmark, Paired Bootstrap (B=10,000) & Gate G1."""

from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.gate_g1_decider import decide_gate_g1
from src.evaluation.paired_bootstrap import run_paired_cluster_bootstrap
from src.evaluation.temporal_benchmark import evaluate_temporal_cohort
from src.features.pre_t0_store import build_pre_t0_features
from src.models.train_candidates import train_candidate_models
from src.rules.rules_engine import calculate_rules_score


def main() -> int:
    enq_path = PROJECT_ROOT / "data" / "raw" / "enquiries_snapshot.csv"
    out_path = PROJECT_ROOT / "data" / "raw" / "outcome_events.csv"
    if not enq_path.exists() or not out_path.exists():
        print("Không tìm thấy CSV trong data/raw/")
        return 1

    enq_df = pd.read_csv(enq_path, dtype=str).fillna("")
    out_df = pd.read_csv(out_path, dtype=str).fillna("")

    # Lọc các dòng hợp lệ có t0_time và product_family chuẩn
    enq_df = enq_df.drop(columns=[c for c in enq_df.columns if c.endswith("_DO_NOT_USE")])
    enq_df = enq_df[
        (enq_df["t0_time"].str.strip() != "")
        & (enq_df["product_family"].isin({"Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"}))
    ].copy()

    cutoff = "2026-08-31T23:59:59+07:00"
    trained = train_candidate_models(
        enq_df,
        out_df,
        training_cutoff_t0=cutoff,
        artifact_dir=PROJECT_ROOT / "artifacts" / "models",
        enforce_g0_gate=False,
        data_origin="SYNTHETIC",
    )

    merged = enq_df.merge(out_df[["enquiry_id", "label"]], on="enquiry_id", how="inner")
    feat_df, _ = build_pre_t0_features(merged, strict_leakage=False, drop_post_t0_rows=True)
    merged = merged[merged["enquiry_id"].isin(set(feat_df["enquiry_id"]))].copy()

    x_mat = trained["preprocessor"].transform(feat_df)
    merged["model_score"] = trained["logreg"].predict_scores(x_mat)
    merged["baseline_score"] = [
        float(calculate_rules_score(r)["rules_score"]) for r in merged.to_dict(orient="records")
    ]

    lock_file = PROJECT_ROOT / "artifacts" / "gate_g1" / "sealed_cohort_locks.json"
    bench = evaluate_temporal_cohort(
        merged,
        training_cutoff_t0=cutoff,
        capacity_k=25,
        lock_file=lock_file,
    )
    boot = run_paired_cluster_bootstrap(
        bench["sealed_df"],
        set(bench["baseline_selected_df"]["enquiry_id"]),
        set(bench["model_selected_df"]["enquiry_id"]),
        n_bootstrap=10000,
        random_state=42,
    )
    report_path = PROJECT_ROOT / "artifacts" / "gate_g1" / "gate_g1_benchmark_report.json"
    rep = decide_gate_g1(
        g1_c1_pipeline_audit_passed=True,
        g1_c2_baseline_ready=True,
        g1_c3_controls_passed_count=40,
        benchmark_result=bench,
        bootstrap_result=boot,
        g1_c7_reviewer_signed_off=False,
        data_origin="SYNTHETIC",
        model_version=trained["manifest"]["model_version"],
        report_path=report_path,
    )
    print(
        f"Gate G1 Report written to {report_path}: "
        f"decision={rep['gate_g1_decision']}, mode={rep['active_serving_mode']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

