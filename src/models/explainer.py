"""PLPT-26: Local Feature Contribution Explainer (Top-3 Signed Contributions)."""

from typing import Any

import numpy as np
import pandas as pd

from src.models.train_candidates import (
    RegularizedLogisticRegressionModel,
    ShallowDecisionTreeModel,
    TabularPreprocessor,
)

CAUSAL_DISCLAIMER = (
    "Contributing factors reflect historical qualification associations at t0, "
    "not causal proof that contacting a lead will result in a sale."
)


def explain_logreg_row(
    model: RegularizedLogisticRegressionModel,
    preprocessor: TabularPreprocessor,
    row_df: pd.DataFrame,
    raw_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trích xuất top-3 đóng góp đặc trưng có dấu (w_j * x'_j) cho Logistic Regression."""
    x_row = preprocessor.transform(row_df)[0]
    contribs_by_raw: dict[str, float] = {}
    for idx, raw_col in enumerate(preprocessor.raw_feature_map):
        val = float(model.coef_[idx] * x_row[idx])
        contribs_by_raw[raw_col] = contribs_by_raw.get(raw_col, 0.0) + val

    top3 = sorted(
        contribs_by_raw.items(),
        key=lambda kv: (-abs(kv[1]), kv[0]),
    )[:3]

    source_row = raw_row or row_df.iloc[0].to_dict()
    factors = [
        {
            "feature": col,
            "contribution": round(val, 6),
            "value": source_row.get(col),
            "feature_event_time": source_row.get("feature_event_time") or source_row.get("t0_time"),
        }
        for col, val in top3
    ]
    return {
        "contributing_factors": factors,
        "disclaimer": CAUSAL_DISCLAIMER,
    }


def explain_tree_row(
    model: ShallowDecisionTreeModel,
    preprocessor: TabularPreprocessor,
    row_df: pd.DataFrame,
    raw_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trích xuất top-3 đóng góp theo đường đi từ gốc đến lá cho Decision Tree."""
    x_row = preprocessor.transform(row_df)[0]
    _, path_deltas = model.predict_one_with_path(x_row)

    contribs_by_raw: dict[str, float] = {}
    for feat_idx, delta in path_deltas:
        raw_col = preprocessor.raw_feature_map[feat_idx]
        contribs_by_raw[raw_col] = contribs_by_raw.get(raw_col, 0.0) + float(delta)

    top3 = sorted(
        contribs_by_raw.items(),
        key=lambda kv: (-abs(kv[1]), kv[0]),
    )[:3]

    source_row = raw_row or row_df.iloc[0].to_dict()
    factors = [
        {
            "feature": col,
            "contribution": round(val, 6),
            "value": source_row.get(col),
            "feature_event_time": source_row.get("feature_event_time") or source_row.get("t0_time"),
        }
        for col, val in top3
    ]
    return {
        "contributing_factors": factors,
        "disclaimer": CAUSAL_DISCLAIMER,
    }
