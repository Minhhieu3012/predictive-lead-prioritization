"""PLPT-28: Paired Cluster Bootstrap Confidence Interval (B=10,000, random_state=42)."""

from typing import Any

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
    """
    Thực hiện Paired Cluster Bootstrap (B = 10,000 mặc định, random_state = 42)
    gom cụm theo (cohort_date_block, account_key) để tính khoảng tin cậy 95% (2.5% - 97.5%)
    cho Delta = Precision@K(model) - Precision@K(baseline).
    Nếu n_clusters < min_clusters, trả sufficient_clusters=False và ép ci_lower_bound_exceeds_zero=False.
    """
    if sealed_df.empty:
        return {
            "n_bootstrap": n_bootstrap,
            "random_state": random_state,
            "min_clusters": min_clusters,
            "n_clusters": 0,
            "sufficient_clusters": False,
            "paired_diff_mean": 0.0,
            "paired_95_ci": [0.0, 0.0],
            "ci_lower_bound_exceeds_zero": False,
        }

    df = sealed_df.copy()
    df["cohort_date_block"] = df["t0_time"].astype(str).str.slice(0, 10)
    df["is_pos"] = (df["label"].astype(str).str.strip().str.upper() == "POSITIVE").astype(float)
    df["in_base"] = df["enquiry_id"].isin(baseline_selected_ids).astype(float)
    df["in_model"] = df["enquiry_id"].isin(model_selected_ids).astype(float)
    df["base_tp"] = df["in_base"] * df["is_pos"]
    df["model_tp"] = df["in_model"] * df["is_pos"]

    grouped = (
        df.groupby(["cohort_date_block", "account_key"], sort=True)[
            ["in_base", "base_tp", "in_model", "model_tp"]
        ]
        .sum()
        .to_numpy(dtype=float)
    )

    n_clusters = len(grouped)
    if n_clusters < min_clusters:
        return {
            "n_bootstrap": n_bootstrap,
            "random_state": random_state,
            "min_clusters": min_clusters,
            "n_clusters": n_clusters,
            "sufficient_clusters": False,
            "paired_diff_mean": 0.0,
            "paired_95_ci": [0.0, 0.0],
            "ci_lower_bound_exceeds_zero": False,
        }

    rng = np.random.default_rng(random_state)
    diffs = np.empty(n_bootstrap, dtype=float)
    for b in range(n_bootstrap):
        idx = rng.integers(0, n_clusters, size=n_clusters)
        sample_sums = np.sum(grouped[idx], axis=0)
        base_n, base_tp, model_n, model_tp = sample_sums
        p_base = (base_tp / base_n) if base_n > 0 else 0.0
        p_model = (model_tp / model_n) if model_n > 0 else 0.0
        diffs[b] = p_model - p_base

    ci_low = float(np.percentile(diffs, 2.5))
    ci_high = float(np.percentile(diffs, 97.5))
    mean_diff = float(np.mean(diffs))

    return {
        "n_bootstrap": n_bootstrap,
        "random_state": random_state,
        "min_clusters": min_clusters,
        "n_clusters": n_clusters,
        "sufficient_clusters": True,
        "paired_diff_mean": round(mean_diff, 4),
        "paired_95_ci": [round(ci_low, 4), round(ci_high, 4)],
        "ci_lower_bound_exceeds_zero": ci_low > 0.0,
    }

