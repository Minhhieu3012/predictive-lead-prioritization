"""Gate G1 decision logic for enabling PREDICTIVE_ML."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Literal

RULES_BASELINE = "RULES_BASELINE"
PREDICTIVE_ML = "PREDICTIVE_ML"
PASS = "PASS"
FAIL = "FAIL"

MINIMUM_LIFT = 1.20
MINIMUM_RELATIVE_LIFT_CI_LOWER_BOUND = 1.0
DEFAULT_MINIMUM_SAMPLE_SIZE = 200

NON_CAUSAL_WARNING = (
    "NON_CAUSAL_WARNING: This evaluation measures predictive performance only. "
    "The observed Precision@K improvement does not establish causal impact "
    "on business outcomes."
)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _population_key(metadata: Mapping[str, object] | None) -> str | None:
    if metadata is None:
        return None
    value = (
        metadata.get("population_id")
        or metadata.get("evaluation_population_id")
        or metadata.get("test_set_id")
    )
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def evaluate_gate_g1(
    *,
    precision_at_k_ml: float | None,
    precision_at_k_baseline: float | None,
    k: int,
    sample_size: int,
    ci_95_lower_bound: float | None,
    ci_95_upper_bound: float | None,
    evaluation_metadata: Mapping[str, object] | None = None,
    minimum_lift: float = MINIMUM_LIFT,
    minimum_sample_size: int = DEFAULT_MINIMUM_SAMPLE_SIZE,
) -> dict[str, object]:
    """Evaluate Gate G1 using a supplied, statistically evaluated relative lift CI."""

    metadata = dict(evaluation_metadata or {})
    base_result: dict[str, object] = {
        "selected_strategy": RULES_BASELINE,
        "gate_status": FAIL,
        "precision_at_k_ml": precision_at_k_ml,
        "precision_at_k_baseline": precision_at_k_baseline,
        "lift": None,
        "ci_95_lower_bound": ci_95_lower_bound,
        "ci_95_upper_bound": ci_95_upper_bound,
        "decision_reason": "",
        "warnings": [NON_CAUSAL_WARNING],
        "evaluation_metadata": metadata,
    }

    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        base_result["decision_reason"] = "INVALID_K"
        return base_result
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < minimum_sample_size
    ):
        base_result["decision_reason"] = "INSUFFICIENT_SAMPLE_SIZE"
        return base_result
    if minimum_lift <= 0 or not math.isfinite(minimum_lift):
        base_result["decision_reason"] = "INVALID_MINIMUM_LIFT"
        return base_result

    if not _is_finite_number(precision_at_k_ml):
        base_result["decision_reason"] = "INVALID_ML_PRECISION_AT_K"
        return base_result
    if not _is_finite_number(precision_at_k_baseline):
        base_result["decision_reason"] = "INVALID_BASELINE_PRECISION_AT_K"
        return base_result

    ml_precision = float(precision_at_k_ml)
    baseline_precision = float(precision_at_k_baseline)
    if not 0 <= ml_precision <= 1:
        base_result["decision_reason"] = "INVALID_ML_PRECISION_AT_K"
        return base_result
    if not 0 <= baseline_precision <= 1:
        base_result["decision_reason"] = "INVALID_BASELINE_PRECISION_AT_K"
        return base_result
    if baseline_precision == 0:
        base_result["decision_reason"] = "ZERO_BASELINE_PRECISION"
        return base_result

    if not _is_finite_number(ci_95_lower_bound):
        base_result["decision_reason"] = "MISSING_OR_INVALID_CONFIDENCE_INTERVAL"
        return base_result
    if not _is_finite_number(ci_95_upper_bound):
        base_result["decision_reason"] = "MISSING_OR_INVALID_CONFIDENCE_INTERVAL"
        return base_result

    lower_bound = float(ci_95_lower_bound)
    upper_bound = float(ci_95_upper_bound)
    if lower_bound > upper_bound:
        base_result["decision_reason"] = "INVALID_CONFIDENCE_INTERVAL_ORDER"
        return base_result

    ml_population = metadata.get("ml_population_id")
    baseline_population = metadata.get("baseline_population_id")
    if ml_population is not None or baseline_population is not None:
        ml_key = str(ml_population).strip() if ml_population is not None else ""
        baseline_key = (
            str(baseline_population).strip()
            if baseline_population is not None
            else ""
        )
        if not ml_key or not baseline_key or ml_key != baseline_key:
            base_result["decision_reason"] = "DIFFERENT_EVALUATION_POPULATIONS"
            return base_result

    population_key = _population_key(metadata)
    if population_key is None:
        base_result["decision_reason"] = "MISSING_EVALUATION_POPULATION"
        return base_result

    ci_method = str(metadata.get("ci_method") or "").strip()
    if ci_method not in {"paired_bootstrap_percentile", "PERCENTILE_BOOTSTRAP"}:
        base_result["decision_reason"] = "UNSUPPORTED_OR_UNPROVEN_CONFIDENCE_INTERVAL"
        return base_result

    lift = ml_precision / baseline_precision
    base_result["lift"] = lift

    if lift >= minimum_lift and lower_bound > MINIMUM_RELATIVE_LIFT_CI_LOWER_BOUND:
        base_result["selected_strategy"] = PREDICTIVE_ML
        base_result["gate_status"] = PASS
        base_result["decision_reason"] = "GATE_G1_PASS"
        base_result["warnings"] = []
        return base_result

    if lift < minimum_lift:
        base_result["decision_reason"] = "GATE_G1_MINIMUM_LIFT_NOT_MET"
    else:
        base_result["decision_reason"] = "GATE_G1_CI_LOWER_BOUND_NOT_ABOVE_NEUTRAL"
    return base_result


def paired_bootstrap_lift_ci(
    *,
    ml_top_k_hits: Sequence[int | float],
    baseline_top_k_hits: Sequence[int | float],
    k: int,
    sample_size: int,
    n_bootstrap: int = 5000,
    seed: int = 42,
    minimum_sample_size: int = DEFAULT_MINIMUM_SAMPLE_SIZE,
) -> tuple[float, float]:
    """Calculate paired percentile bootstrap CI for relative Precision@K lift.

    Each sequence contains paired binary relevance indicators for the same
    K evaluation positions. Rows are resampled by index, preserving pairing.
    """

    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < minimum_sample_size
    ):
        raise ValueError(
            f"sample_size must be an integer of at least {minimum_sample_size}"
        )
    if len(ml_top_k_hits) != k or len(baseline_top_k_hits) != k:
        raise ValueError("ML and baseline arrays must contain exactly K values")
    if len(ml_top_k_hits) != len(baseline_top_k_hits):
        raise ValueError("ML and baseline arrays must represent paired samples")
    if isinstance(n_bootstrap, bool) or not isinstance(n_bootstrap, int) or n_bootstrap < 100:
        raise ValueError("n_bootstrap must be an integer of at least 100")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    ml_hits: list[float] = []
    baseline_hits: list[float] = []
    for ml_hit, baseline_hit in zip(ml_top_k_hits, baseline_top_k_hits):
        if isinstance(ml_hit, bool) or isinstance(baseline_hit, bool):
            raise ValueError("Top-K relevance indicators must be 0 or 1")
        ml_value = float(ml_hit)
        baseline_value = float(baseline_hit)
        if not math.isfinite(ml_value) or not math.isfinite(baseline_value):
            raise ValueError("Top-K relevance indicators must be finite")
        if ml_value not in (0.0, 1.0) or baseline_value not in (0.0, 1.0):
            raise ValueError("Top-K relevance indicators must be 0 or 1")
        ml_hits.append(ml_value)
        baseline_hits.append(baseline_value)

    rng = random.Random(seed)
    lifts: list[float] = []
    for _ in range(n_bootstrap):
        ml_positive = 0.0
        baseline_positive = 0.0
        for _ in range(k):
            index = rng.randrange(k)
            ml_positive += ml_hits[index]
            baseline_positive += baseline_hits[index]

        ml_precision = ml_positive / k
        baseline_precision = baseline_positive / k
        if baseline_precision > 0:
            lifts.append(ml_precision / baseline_precision)

    minimum_successful_resamples = max(100, n_bootstrap // 2)
    if len(lifts) < minimum_successful_resamples:
        raise ValueError("Insufficient successful degenerate-bootstrap resamples")

    lifts.sort()
    lower_index = int(math.floor(0.025 * (len(lifts) - 1)))
    upper_index = int(math.floor(0.975 * (len(lifts) - 1)))
    lower = lifts[lower_index]
    upper = lifts[upper_index]
    if not math.isfinite(lower) or not math.isfinite(upper):
        raise ValueError("Bootstrap produced invalid confidence interval bounds")
    return lower, upper


def ensure_predictive_ml_allowed(gate_result: Mapping[str, object] | None) -> bool:
    """Central guard for any PREDICTIVE_ML activation path."""

    if gate_result is None:
        return False
    return (
        gate_result.get("gate_status") == PASS
        and gate_result.get("selected_strategy") == PREDICTIVE_ML
        and gate_result.get("lift") is not None
    )
