import pytest

from src.ml.gate_g1_decider import (
    FAIL,
    NON_CAUSAL_WARNING,
    PASS,
    PREDICTIVE_ML,
    RULES_BASELINE,
    ensure_predictive_ml_allowed,
    evaluate_gate_g1,
    paired_bootstrap_lift_ci,
)
from src.queue_allocation import allocate_ranked_enquiries


def metadata(**overrides):
    value = {
        "population_id": "independent-test-200",
        "ml_population_id": "independent-test-200",
        "baseline_population_id": "independent-test-200",
        "ci_method": "paired_bootstrap_percentile",
        "k": 25,
        "sample_size": 200,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("ml_precision", "baseline_precision", "lower_bound"),
    [
        (0.30, 0.20, 1.10),
        (0.24, 0.20, 1.01),
    ],
)
def test_gate_passes_lift_above_or_equal_threshold(
    ml_precision,
    baseline_precision,
    lower_bound,
):
    result = evaluate_gate_g1(
        precision_at_k_ml=ml_precision,
        precision_at_k_baseline=baseline_precision,
        k=25,
        sample_size=200,
        ci_95_lower_bound=lower_bound,
        ci_95_upper_bound=1.80,
        evaluation_metadata=metadata(),
    )

    assert result["gate_status"] == PASS
    assert result["selected_strategy"] == PREDICTIVE_ML


@pytest.mark.parametrize(
    ("ml_precision", "baseline_precision", "lower_bound", "reason"),
    [
        (0.20, 0.20, 1.10, "GATE_G1_MINIMUM_LIFT_NOT_MET"),
        (0.30, 0.20, 1.00, "GATE_G1_CI_LOWER_BOUND_NOT_ABOVE_NEUTRAL"),
        (0.30, 0.20, 0.90, "GATE_G1_CI_LOWER_BOUND_NOT_ABOVE_NEUTRAL"),
        (0.30, 0.20, None, "MISSING_OR_INVALID_CONFIDENCE_INTERVAL"),
        (None, 0.20, 1.10, "INVALID_ML_PRECISION_AT_K"),
        (0.30, 0.0, 1.10, "ZERO_BASELINE_PRECISION"),
        (0.30, float("nan"), 1.10, "INVALID_BASELINE_PRECISION_AT_K"),
        (1.20, 0.20, 1.10, "INVALID_ML_PRECISION_AT_K"),
    ],
)
def test_gate_fails_invalid_or_negative_evidence(
    ml_precision,
    baseline_precision,
    lower_bound,
    reason,
):
    result = evaluate_gate_g1(
        precision_at_k_ml=ml_precision,
        precision_at_k_baseline=baseline_precision,
        k=25,
        sample_size=200,
        ci_95_lower_bound=lower_bound,
        ci_95_upper_bound=1.80,
        evaluation_metadata=metadata(),
    )

    assert result["gate_status"] == FAIL
    assert result["selected_strategy"] == RULES_BASELINE
    assert result["decision_reason"] == reason
    assert NON_CAUSAL_WARNING in result["warnings"]


def test_invalid_population_and_insufficient_sample_fail():
    insufficient = evaluate_gate_g1(
        precision_at_k_ml=0.30,
        precision_at_k_baseline=0.20,
        k=25,
        sample_size=199,
        ci_95_lower_bound=1.10,
        ci_95_upper_bound=1.80,
        evaluation_metadata=metadata(),
    )
    assert insufficient["decision_reason"] == "INSUFFICIENT_SAMPLE_SIZE"

    different_population = evaluate_gate_g1(
        precision_at_k_ml=0.30,
        precision_at_k_baseline=0.20,
        k=25,
        sample_size=200,
        ci_95_lower_bound=1.10,
        ci_95_upper_bound=1.80,
        evaluation_metadata=metadata(
            baseline_population_id="different-test-set"
        ),
    )
    assert different_population["decision_reason"] == "DIFFERENT_EVALUATION_POPULATIONS"


def test_missing_ci_never_unlocks_ml():
    result = evaluate_gate_g1(
        precision_at_k_ml=0.30,
        precision_at_k_baseline=0.20,
        k=25,
        sample_size=200,
        ci_95_lower_bound=None,
        ci_95_upper_bound=None,
        evaluation_metadata=metadata(),
    )
    assert not ensure_predictive_ml_allowed(result)
    assert result["selected_strategy"] == RULES_BASELINE


def test_paired_bootstrap_is_reproducible():
    ml_hits = [1] * 15 + [0] * 10
    baseline_hits = [1] * 10 + [0] * 15

    first = paired_bootstrap_lift_ci(
        ml_top_k_hits=ml_hits,
        baseline_top_k_hits=baseline_hits,
        k=25,
        sample_size=200,
        n_bootstrap=1000,
        seed=42,
    )
    second = paired_bootstrap_lift_ci(
        ml_top_k_hits=ml_hits,
        baseline_top_k_hits=baseline_hits,
        k=25,
        sample_size=200,
        n_bootstrap=1000,
        seed=42,
    )

    assert first == second
    assert first[0] >= 1.0


def test_allocation_fallback_blocks_unauthorized_predictive_ml():
    items = [
        {
            "enquiry_id": "SN-001",
            "cohort": "A",
            "cohort_rank": 1,
            "score_mode": "PREDICTIVE_ML",
            "score_unit": "points",
            "rules_score": 80,
            "tier0_bypass": False,
            "is_quarantined": False,
            "is_scored": True,
        }
    ]

    failed_gate = evaluate_gate_g1(
        precision_at_k_ml=0.20,
        precision_at_k_baseline=0.20,
        k=25,
        sample_size=200,
        ci_95_lower_bound=1.10,
        ci_95_upper_bound=1.30,
        evaluation_metadata=metadata(),
    )
    allocation = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=25,
        seed=42,
        gate_decision=failed_gate,
    )

    assert allocation["selected"][0]["score_mode"] == RULES_BASELINE
    assert allocation["selected"][0]["gate_warning"] == NON_CAUSAL_WARNING
    assert allocation["gate_decision"]["selected_strategy"] == RULES_BASELINE


def test_allocation_accepts_only_passing_gate():
    items = [
        {
            "enquiry_id": "SN-001",
            "cohort": "A",
            "cohort_rank": 1,
            "score_mode": "PREDICTIVE_ML",
            "score_unit": "points",
            "rules_score": 80,
            "tier0_bypass": False,
            "is_quarantined": False,
            "is_scored": True,
        }
    ]

    passing_gate = evaluate_gate_g1(
        precision_at_k_ml=0.30,
        precision_at_k_baseline=0.20,
        k=25,
        sample_size=200,
        ci_95_lower_bound=1.10,
        ci_95_upper_bound=1.80,
        evaluation_metadata=metadata(),
    )
    allocation = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=25,
        seed=42,
        gate_decision=passing_gate,
    )

    assert allocation["selected"][0]["score_mode"] == PREDICTIVE_ML
