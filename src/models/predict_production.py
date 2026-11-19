"""PLPT-26 / CF-06: Production Batch Scoring Isolated from outcome_events."""

import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

from src.features.pre_t0_store import build_pre_t0_features, guard_no_outcome_join
from src.models.train_candidates import RegularizedLogisticRegressionModel, TabularPreprocessor


def score_production_batch(
    db_path: str | Path,
    manifest_path: str | Path,
    model: RegularizedLogisticRegressionModel,
    preprocessor: TabularPreprocessor,
    *,
    requested_tables: list[str] | None = None,
) -> pd.DataFrame:
    """
    Chấm điểm batch trên Production:
    - CHỈ được phép đọc `clean_enquiries` + `pre_t0_store` + `manifest.json`.
    - Tuyệt đối KHÔNG truy vấn `outcome_events` hoặc `raw_outcomes` (nâng LeakageError nếu cố tình).
    """
    if requested_tables:
        for tbl in requested_tables:
            if "outcome" in tbl.lower():
                guard_no_outcome_join(["label"])

    m_path = Path(manifest_path)
    if not m_path.exists():
        raise FileNotFoundError(f"Manifest not found: {m_path}")
    manifest = json.loads(m_path.read_text(encoding="utf-8"))

    with sqlite3.connect(str(db_path)) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM clean_enquiries WHERE COALESCE(LOWER(TRIM(workflow_state)), '') NOT IN ('closed', 'withdrawn')",
            conn,
        )

    if df.empty:
        return pd.DataFrame(columns=["enquiry_id", "model_score", "model_version", "scoring_mode"])

    guard_no_outcome_join(list(df.columns))
    feat_df, _ = build_pre_t0_features(df, strict_leakage=True)
    x_mat = preprocessor.transform(feat_df)
    scores = model.predict_scores(x_mat)

    out = pd.DataFrame(
        {
            "enquiry_id": feat_df["enquiry_id"].astype(str),
            "model_score": scores,
            "model_version": manifest.get("model_version", "unknown"),
            "scoring_mode": manifest.get("serving_mode_locked", "RULES_BASELINE"),
        }
    )
    return out
