"""PLPT-28: Gate G1 Release Decider (G1-C1 to G1-C7) & Mandatory Rules Fallback."""

import json
from pathlib import Path
from typing import Any

MIN_LIFT_THRESHOLD = 1.20
DISCLAIMER = (
    "Offline ranking lift reflects historical qualification association "
    "and does not establish causal sales uplift."
)


def decide_gate_g1(
    *,
    g1_c1_pipeline_audit_passed: bool,
    g1_c2_baseline_ready: bool,
    g1_c3_controls_passed_count: int,
    benchmark_result: dict[str, Any],
    bootstrap_result: dict[str, Any],
    g1_c7_reviewer_signed_off: bool = False,
    data_origin: str = "SYNTHETIC",
    model_version: str = "plpt-logreg-v1.0.0",
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Đánh giá đồng thời cả 7 tiêu chí G1-C1..G1-C7:
    - Nếu là dữ liệu SYNTHETIC hoặc bất kỳ tiêu chí nào không đạt -> FALLBACK_RULES_BASELINE.
    - Chỉ mở khóa APPROVED_PREDICTIVE_ML khi dữ liệu REAL đạt đủ G1-C1..G1-C7.
    """
    lift = benchmark_result.get("precision_at_k_lift")
    ci_exceeds_zero = bool(bootstrap_result.get("ci_lower_bound_exceeds_zero", False))
    coverage_met = bool(benchmark_result.get("coverage_gate_met", False))
    floor_met = bool(benchmark_result.get("floor_met", False))

    criteria = {
        "G1-C1": bool(g1_c1_pipeline_audit_passed),
        "G1-C2": bool(g1_c2_baseline_ready),
        "G1-C3": int(g1_c3_controls_passed_count) == 40,
        "G1-C4": coverage_met and floor_met,
        "G1-C5": (lift is not None) and (float(lift) >= MIN_LIFT_THRESHOLD),
        "G1-C6": ci_exceeds_zero,
        "G1-C7": bool(g1_c7_reviewer_signed_off),
        "REAL_DATA_VERIFIED": str(data_origin).strip().upper() == "REAL",
    }

    failed_reasons = [k for k, passed in criteria.items() if not passed]
    all_passed = len(failed_reasons) == 0

    decision = "APPROVED_PREDICTIVE_ML" if all_passed else "FALLBACK_RULES_BASELINE"
    active_serving_mode = "PREDICTIVE_ML" if all_passed else "RULES_BASELINE"

    report = {
        "evaluation_version": "gate-g1-eval-v1.0.0",
        "data_origin": data_origin,
        "model_version": model_version,
        "capacity_k": benchmark_result.get("capacity_k"),
        "cohort_counts": benchmark_result.get("cohort_counts"),
        "sealed_cohort_fingerprint": benchmark_result.get("sealed_cohort_fingerprint"),
        "rules_baseline_metrics": benchmark_result.get("rules_baseline_metrics"),
        "candidate_model_metrics": benchmark_result.get("candidate_model_metrics"),
        "segment_breakdown": benchmark_result.get("segment_breakdown"),
        "lift_and_uncertainty": {
            "precision_at_k_lift": lift,
            "paired_diff_mean": bootstrap_result.get("paired_diff_mean"),
            "paired_95_ci": bootstrap_result.get("paired_95_ci"),
            "ci_lower_bound_exceeds_zero": ci_exceeds_zero,
        },
        "criteria_results": criteria,
        "failed_criteria": failed_reasons,
        "gate_g1_decision": decision,
        "active_serving_mode": active_serving_mode,
        "disclaimer": DISCLAIMER,
    }

    if report_path is not None:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    return report
