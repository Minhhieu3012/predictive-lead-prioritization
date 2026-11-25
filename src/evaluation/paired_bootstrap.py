"""Paired bootstrap evaluation for time-blocked, account-clustered benchmarks.

The bootstrap resamples (time block, account_key) clusters with replacement.
Candidate and baseline metrics are computed on the *same* sampled rows, so
comparisons remain paired and account/time clustering is preserved.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any


ClusterMetric = Callable[[Sequence[Mapping[str, Any]], int | None], float]


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _time_block(row: Mapping[str, Any], time_key: str) -> str:
    value = _value(row, time_key, "time_block", "cohort")
    if value is None:
        value = _value(row, "t0_time", "t0")
    text = str(value).strip()
    if not text:
        return "UNKNOWN_TIME"
    # ISO timestamps are grouped into UTC/calendar-style day blocks.
    return text[:10]


def _positive_label(row: Mapping[str, Any], label_key: str) -> bool:
    value = _value(row, label_key, "label", "target", "y_true")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) == 1.0
    text = str(value).strip().upper()
    return text in {"1", "TRUE", "YES", "POSITIVE", "Y"}


def _numeric(row: Mapping[str, Any], *names: str) -> float:
    value = _value(row, *names)
    if isinstance(value, bool):
        return float(value)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def precision_at_k(rows: Sequence[Mapping[str, Any]], k: int | None,
                   candidate_key: str = "candidate_score",
                   label_key: str = "label") -> float:
    """Precision@K: fraction of positive labels among the top K rows."""
    if not rows:
        return 0.0
    limit = len(rows) if k is None else max(0, min(int(k), len(rows)))
    if limit == 0:
        return 0.0
    ranked = sorted(rows, key=lambda row: _numeric(row, candidate_key), reverse=True)
    top = ranked[:limit]
    return sum(_positive_label(row, label_key) for row in top) / float(limit)


def check_mature_label_coverage(
    rows: Iterable[Mapping[str, Any]],
    threshold: float = 0.90,
    maturity_key: str = "maturity_state",
) -> dict[str, Any]:
    """Check whether at least 90% of benchmark rows have mature labels."""
    materialized = list(rows)
    total = len(materialized)
    mature = 0
    for row in materialized:
        value = _value(row, maturity_key)
        maturity = str(value).strip().upper() if value is not None else ""
        if maturity == "MATURE":
            mature += 1

    coverage = (mature / total) if total else 0.0
    passed = coverage >= threshold
    return {
        "mature_count": mature,
        "total_count": total,
        "coverage": coverage,
        "threshold": threshold,
        "passed": passed,
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def paired_bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    candidate_score_key: str = "candidate_score",
    baseline_score_key: str = "baseline_score",
    *,
    k: int | None = None,
    n_bootstrap: int = 10_000,
    seed: int = 42,
    confidence: float = 0.95,
    time_key: str = "time_block",
    account_key: str = "account_key",
    label_key: str = "label",
    mature_only: bool = True,
    mature_threshold: float = 0.90,
) -> dict[str, Any]:
    """Estimate paired Precision@K lift with clustered 95% bootstrap CI.

    Resampling units are ``(time block, account_key)``. The candidate and
    baseline scores are evaluated on exactly the same bootstrap sample.
    """
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be at least 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")

    data = [dict(row) for row in rows]
    if mature_only:
        data = [
            row for row in data
            if str(_value(row, "maturity_state") or "").strip().upper() == "MATURE"
        ]

    coverage = check_mature_label_coverage(rows, threshold=mature_threshold)
    if not coverage["passed"]:
        raise ValueError(
            "Mature label coverage "
            f"{coverage['coverage']:.2%} is below required {mature_threshold:.2%}"
        )
    if not data:
        raise ValueError("No mature benchmark rows are available")

    clusters: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in data:
        account = str(_value(row, account_key) or "UNKNOWN_ACCOUNT").strip()
        block = _time_block(row, time_key)
        clusters[(block, account)].append(row)

    cluster_values = list(clusters.values())
    rng = random.Random(seed)

    candidate_lifts: list[float] = []
    lift_diffs: list[float] = []
    for _ in range(n_bootstrap):
        sample: list[dict[str, Any]] = []
        for _cluster_index in range(len(cluster_values)):
            sample.extend(rng.choice(cluster_values))

        candidate_precision = precision_at_k(
            sample, k, candidate_score_key, label_key
        )
        baseline_precision = precision_at_k(
            sample, k, baseline_score_key, label_key
        )
        lift = (
            candidate_precision / baseline_precision
            if baseline_precision > 0.0
            else (math.inf if candidate_precision > 0.0 else 1.0)
        )
        candidate_lifts.append(lift)
        lift_diffs.append(candidate_precision - baseline_precision)

    finite_lifts = [value for value in candidate_lifts if math.isfinite(value)]
    if not finite_lifts:
        raise ValueError("Unable to form a finite lift confidence interval")

    tail = (1.0 - confidence) / 2.0
    full_candidate = precision_at_k(data, k, candidate_score_key, label_key)
    full_baseline = precision_at_k(data, k, baseline_score_key, label_key)
    full_lift = (
        full_candidate / full_baseline
        if full_baseline > 0.0
        else (math.inf if full_candidate > 0.0 else 1.0)
    )

    return {
        "candidate_precision_at_k": full_candidate,
        "baseline_precision_at_k": full_baseline,
        "lift": full_lift,
        "lift_ci": (
            _percentile(finite_lifts, tail),
            _percentile(finite_lifts, 1.0 - tail),
        ),
        "lift_difference_ci": (
            _percentile(lift_diffs, tail),
            _percentile(lift_diffs, 1.0 - tail),
        ),
        "ci_95": confidence == 0.95,
        "confidence": confidence,
        "n_bootstrap": n_bootstrap,
        "seed": seed,
        "cluster_count": len(cluster_values),
        "mature_label_coverage": coverage,
        "gate_g1_passed": coverage["passed"]
        and math.isfinite(full_lift)
        and full_lift >= 1.20
        and _percentile(finite_lifts, tail) > 0.0,
    }


def estimate_paired_bootstrap_ci(
    rows: Sequence[Mapping[str, Any]],
    candidate_scores: str | Sequence[float] = "candidate_score",
    baseline_scores: str | Sequence[float] = "baseline_score",
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience wrapper supporting column names or paired score sequences."""
    if isinstance(candidate_scores, str) or isinstance(baseline_scores, str):
        return paired_bootstrap_ci(
            rows,
            str(candidate_scores),
            str(baseline_scores),
            **kwargs,
        )

    if len(candidate_scores) != len(rows) or len(baseline_scores) != len(rows):
        raise ValueError("Candidate, baseline, and row sequences must have equal length")
    augmented = [dict(row) for row in rows]
    for row, candidate, baseline in zip(augmented, candidate_scores, baseline_scores):
        row["__candidate_score"] = candidate
        row["__baseline_score"] = baseline
    kwargs.setdefault("candidate_score_key", "__candidate_score")
    kwargs.setdefault("baseline_score_key", "__baseline_score")
    # The wrapper keyword names differ from paired_bootstrap_ci for convenience.
    candidate_key = kwargs.pop("candidate_score_key")
    baseline_key = kwargs.pop("baseline_score_key")
    return paired_bootstrap_ci(
        augmented,
        candidate_key,
        baseline_key,
        **kwargs,
    )


# ==============================================================================
# PLPT-28 Paired Cluster Bootstrap
# ==============================================================================

import numpy as np
import pandas as pd


def run_paired_cluster_bootstrap(
    sealed_df: pd.DataFrame,
    baseline_selected_ids: set[str],
    model_selected_ids: set[str],
    *,
    n_bootstrap: int = 10000,
    random_state: int = 42,
    min_clusters: int = 10,
) -> dict[str, Any]:
    if sealed_df.empty:
        return {
            "n_bootstrap": n_bootstrap,
            "random_state": random_state,
            "min_clusters": min_clusters,
            "n_clusters": 0,
            "sufficient_clusters": False,
            "paired_diff_mean": 0.0,
            "paired_95_ci": [0.0, 0.0],
            "mean_delta_precision_at_k": 0.0,
            "ci_lower_2_5": 0.0,
            "ci_upper_97_5": 0.0,
            "ci_lower_bound_exceeds_zero": False,
        }

    df = sealed_df.copy()
    if "cohort_date_block" not in df.columns:
        if "t0_time" in df.columns:
            df["cohort_date_block"] = (
                pd.to_datetime(df["t0_time"], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("UNKNOWN_DATE")
            )
        else:
            df["cohort_date_block"] = "UNKNOWN_DATE"

    if "account_key" not in df.columns:
        df["account_key"] = "UNKNOWN_ACCOUNT"

    df["cluster_id"] = (
        df["cohort_date_block"].astype(str) + "::" + df["account_key"].astype(str)
    )

    df["label_norm"] = df["label"].astype(str).str.strip().str.upper()
    df["y_true"] = (df["label_norm"] == "POSITIVE").astype(int)

    cluster_keys = df["cluster_id"].unique()
    n_clusters = len(cluster_keys)

    if n_clusters < min_clusters:
        return {
            "n_bootstrap": n_bootstrap,
            "random_state": random_state,
            "min_clusters": min_clusters,
            "n_clusters": int(n_clusters),
            "sufficient_clusters": False,
            "paired_diff_mean": 0.0,
            "paired_95_ci": [0.0, 0.0],
            "mean_delta_precision_at_k": 0.0,
            "ci_lower_2_5": 0.0,
            "ci_upper_97_5": 0.0,
            "ci_lower_bound_exceeds_zero": False,
        }

    clusters_dict = {
        cid: group[["enquiry_id", "y_true"]]
        for cid, group in df.groupby("cluster_id")
    }

    base_set = set(baseline_selected_ids)
    model_set = set(model_selected_ids)

    cluster_stats = []
    for cid in cluster_keys:
        grp = clusters_dict[cid]
        b_hits = grp[grp["enquiry_id"].isin(base_set)]["y_true"].sum()
        b_sel = grp["enquiry_id"].isin(base_set).sum()
        m_hits = grp[grp["enquiry_id"].isin(model_set)]["y_true"].sum()
        m_sel = grp["enquiry_id"].isin(model_set).sum()
        cluster_stats.append((b_hits, b_sel, m_hits, m_sel))

    stats_arr = np.array(cluster_stats, dtype=float)

    rng = np.random.default_rng(random_state)
    deltas = np.zeros(n_bootstrap, dtype=float)

    for b in range(n_bootstrap):
        sampled_indices = rng.integers(0, n_clusters, size=n_clusters)
        sampled_stats = stats_arr[sampled_indices]
        total_b_hits = sampled_stats[:, 0].sum()
        total_b_sel = sampled_stats[:, 1].sum()
        total_m_hits = sampled_stats[:, 2].sum()
        total_m_sel = sampled_stats[:, 3].sum()

        p_base = (total_b_hits / total_b_sel) if total_b_sel > 0 else 0.0
        p_model = (total_m_hits / total_m_sel) if total_m_sel > 0 else 0.0
        deltas[b] = p_model - p_base

    ci_lower = float(np.percentile(deltas, 2.5))
    ci_upper = float(np.percentile(deltas, 97.5))
    mean_delta = float(np.mean(deltas))

    return {
        "n_bootstrap": n_bootstrap,
        "random_state": random_state,
        "min_clusters": int(min_clusters),
        "n_clusters": int(n_clusters),
        "sufficient_clusters": True,
        "paired_diff_mean": round(mean_delta, 4),
        "paired_95_ci": [round(ci_lower, 4), round(ci_upper, 4)],
        "mean_delta_precision_at_k": round(mean_delta, 6),
        "ci_lower_2_5": round(ci_lower, 6),
        "ci_upper_97_5": round(ci_upper, 6),
        "ci_lower_bound_exceeds_zero": bool(ci_lower > 0.0),
    }
