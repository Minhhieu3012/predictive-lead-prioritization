"""PLPT-52: Leakage-free supervised candidate model training.

Reads the existing PLPT-51 feature store and outcome tables from SQLite.
The database is opened read-only. This module never writes to the project DB.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier


FEATURE_COLUMNS = [
    "product_family",
    "spec_completeness",
    "is_urgent_replacement",
    "contract_days_to_due_at_t0",
    "industry_at_t0",
    "enquiry_channel_at_t0",
    "region_at_t0",
    "estimated_value_band_at_t0",
    "account_age_days_at_t0",
    "prior_purchase_count_at_t0",
    "prior_enquiry_count",
    "prior_distinct_product_family_count",
    "account_history_status",
]

NUMERIC_FEATURES = [
    "is_urgent_replacement",
    "contract_days_to_due_at_t0",
    "account_age_days_at_t0",
    "prior_purchase_count_at_t0",
    "prior_enquiry_count",
    "prior_distinct_product_family_count",
]

CATEGORICAL_FEATURES = [
    "product_family",
    "spec_completeness",
    "industry_at_t0",
    "enquiry_channel_at_t0",
    "region_at_t0",
    "estimated_value_band_at_t0",
    "account_history_status",
]

CUTOFF = "2025-05-01T00:00:00+07:00"
RANDOM_STATE = 42
MIN_OBSERVATION_DAYS = 60
ALLOWED_LABELS = {"POSITIVE", "NEGATIVE"}


class DatasetError(ValueError):
    """Raised when the source data cannot safely form a training dataset."""


@dataclass
class SplitSummary:
    rows: int
    positives: int
    negatives: int
    min_t0: str | None
    max_t0: str | None


def _readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite database in read-only mode."""
    resolved = Path(db_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database not found: {resolved}")
    uri = resolved.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _get_conflicted_canonicals(
    merge_df: pd.DataFrame, outcomes_df: pd.DataFrame
) -> set[str]:
    """Return canonical IDs whose merged records disagree or lack an outcome."""
    outcome_map = outcomes_df.set_index("enquiry_id").to_dict(orient="index")
    conflicted: set[str] = set()

    for row in merge_df.itertuples(index=False):
        canonical_id = str(row.canonical_enquiry_id)
        merged_id = str(row.merged_enquiry_id)
        canonical_outcome = outcome_map.get(canonical_id)
        merged_outcome = outcome_map.get(merged_id)

        if canonical_outcome is None or merged_outcome is None:
            conflicted.add(canonical_id)
            continue

        canonical_label = canonical_outcome.get("label")
        merged_label = merged_outcome.get("label")
        if pd.isna(canonical_label) or pd.isna(merged_label):
            conflicted.add(canonical_id)
        elif str(canonical_label).strip().upper() != str(merged_label).strip().upper():
            conflicted.add(canonical_id)

    return conflicted


def load_eligible_dataset(db_path: str | Path) -> pd.DataFrame:
    """Load and filter mature TRAINING_CANDIDATE rows from the existing DB.

    SEALED_LATER_TEST and OPEN_24H are excluded before any modeling work.
    Canonicals with conflicting duplicate outcomes are excluded.
    """
    with _readonly_connection(db_path) as conn:
        required_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "pre_t0_features",
            "clean_enquiries",
            "raw_outcomes",
            "merge_audit_log",
        }
        missing = required - required_tables
        if missing:
            raise DatasetError(f"Missing required SQLite tables: {sorted(missing)}")

        features = pd.read_sql_query("SELECT * FROM pre_t0_features", conn)
        enquiries = pd.read_sql_query(
            "SELECT enquiry_id, t0_time, cohort FROM clean_enquiries", conn
        )
        outcomes = pd.read_sql_query(
            """
            SELECT enquiry_id, observation_window_days, maturity_state, label
            FROM raw_outcomes
            """,
            conn,
        )
        merges = pd.read_sql_query(
            """
            SELECT canonical_enquiry_id, merged_enquiry_id
            FROM merge_audit_log
            """,
            conn,
        )

    for name, frame, columns in (
        ("pre_t0_features", features, {"enquiry_id", *FEATURE_COLUMNS}),
        ("clean_enquiries", enquiries, {"enquiry_id", "t0_time", "cohort"}),
        (
            "raw_outcomes",
            outcomes,
            {"enquiry_id", "observation_window_days", "maturity_state", "label"},
        ),
        ("merge_audit_log", merges, {"canonical_enquiry_id", "merged_enquiry_id"}),
    ):
        missing_columns = columns - set(frame.columns)
        if missing_columns:
            raise DatasetError(
                f"{name} is missing columns: {sorted(missing_columns)}"
            )

    if features["enquiry_id"].duplicated().any():
        raise DatasetError("pre_t0_features contains duplicate enquiry_id values")
    if enquiries["enquiry_id"].duplicated().any():
        raise DatasetError("clean_enquiries contains duplicate enquiry_id values")
    if outcomes["enquiry_id"].duplicated().any():
        raise DatasetError("raw_outcomes contains duplicate enquiry_id values")

    conflicted = _get_conflicted_canonicals(merges, outcomes)

    merged = features.merge(
        enquiries[["enquiry_id", "t0_time", "cohort"]],
        on="enquiry_id",
        how="inner",
        validate="one_to_one",
    ).merge(
        outcomes,
        on="enquiry_id",
        how="inner",
        validate="one_to_one",
    )

    merged["t0_parsed"] = pd.to_datetime(merged["t0_time"], utc=True, errors="coerce")
    merged["observation_window_days"] = pd.to_numeric(
        merged["observation_window_days"], errors="coerce"
    )
    merged["label"] = merged["label"].astype("string").str.strip().str.upper()
    merged["maturity_state"] = (
        merged["maturity_state"].astype("string").str.strip().str.upper()
    )

    eligible = merged.loc[
        (merged["cohort"] == "TRAINING_CANDIDATE")
        & (merged["maturity_state"] == "MATURE")
        & (merged["observation_window_days"] >= MIN_OBSERVATION_DAYS)
        & (merged["label"].isin(ALLOWED_LABELS))
        & merged["t0_parsed"].notna()
        & (~merged["enquiry_id"].astype(str).isin(conflicted))
    ].copy()

    if eligible.empty:
        raise DatasetError("No eligible mature TRAINING_CANDIDATE rows found")

    # Explicitly select only approved pre-t0 predictors. IDs/labels/timestamps
    # remain metadata and are never passed to the estimator.
    return eligible[
        ["enquiry_id", "t0_time", "t0_parsed", "label", *FEATURE_COLUMNS]
    ].reset_index(drop=True)


def split_by_t0(
    dataset: pd.DataFrame, cutoff: str = CUTOFF
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronologically split rows; train is strictly before cutoff."""
    cutoff_ts = pd.to_datetime(cutoff, utc=True, errors="raise")
    if "t0_parsed" in dataset.columns:
        times = pd.to_datetime(dataset["t0_parsed"], utc=True, errors="coerce")
    else:
        times = pd.to_datetime(dataset["t0_time"], utc=True, errors="coerce")

    if times.isna().any():
        raise DatasetError("Invalid or missing t0_time found during temporal split")

    train = dataset.loc[times < cutoff_ts].copy()
    validation = dataset.loc[times >= cutoff_ts].copy()

    if train.empty or validation.empty:
        raise DatasetError(
            "Temporal split produced an empty train or validation set; "
            f"cutoff={cutoff}"
        )
    if pd.to_datetime(train["t0_time"], utc=True).max() >= pd.to_datetime(
        validation["t0_time"], utc=True
    ).min():
        raise DatasetError("Temporal leakage: train is not strictly earlier than validation")

    return train.reset_index(drop=True), validation.reset_index(drop=True)


def summarize_split(frame: pd.DataFrame) -> SplitSummary:
    times = pd.to_datetime(frame["t0_time"], utc=True, errors="coerce")
    return SplitSummary(
        rows=int(len(frame)),
        positives=int((frame["label"] == "POSITIVE").sum()),
        negatives=int((frame["label"] == "NEGATIVE").sum()),
        min_t0=frame.loc[times.idxmin(), "t0_time"] if len(frame) else None,
        max_t0=frame.loc[times.idxmax(), "t0_time"] if len(frame) else None,
    )


def make_preprocessor() -> ColumnTransformer:
    """Create a fresh preprocessor; it is fitted only inside a train pipeline."""
    numeric_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoder", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("numeric", numeric_pipeline, NUMERIC_FEATURES),
            ("categorical", categorical_pipeline, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )


def make_model_pipelines() -> dict[str, Pipeline]:
    """Return exactly two candidate pipelines, each with its own preprocessor."""
    logistic = Pipeline(
        steps=[
            ("preprocessor", make_preprocessor()),
            (
                "classifier",
                LogisticRegression(
                    penalty="l2",
                    C=1.0,
                    solver="lbfgs",
                    max_iter=1000,
                    random_state=RANDOM_STATE,
                ),
            ),
        ]
    )
    shallow_tree = Pipeline(
        steps=[
            ("preprocessor", make_preprocessor()),
            (
                "classifier",
                DecisionTreeClassifier(
                    max_depth=3,
                    min_samples_leaf=10,
                    random_state=RANDOM_STATE,
                ),
            ),
        ]
    )
    return {
        "regularized_logistic_regression": logistic,
        "shallow_decision_tree": shallow_tree,
    }


def evaluate_model(
    model: Pipeline, x_validation: pd.DataFrame, y_validation: pd.Series
) -> dict[str, Any]:
    """Evaluate without refitting; undefined ranking metrics are reported as None."""
    predictions = model.predict(x_validation)
    probabilities = model.predict_proba(x_validation)[:, 1]
    y_true = np.asarray(y_validation, dtype=int)

    roc_auc: float | None
    if len(np.unique(y_true)) == 2:
        roc_auc = float(roc_auc_score(y_true, probabilities))
    else:
        roc_auc = None

    pr_auc: float | None
    if int(y_true.sum()) > 0:
        pr_auc = float(average_precision_score(y_true, probabilities))
    else:
        pr_auc = None

    matrix = confusion_matrix(y_true, predictions, labels=[0, 1])
    return {
        "accuracy": float(accuracy_score(y_true, predictions)),
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "f1": float(f1_score(y_true, predictions, zero_division=0)),
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "confusion_matrix_labels_0_1": matrix.tolist(),
    }


def run_training(db_path: str | Path, cutoff: str = CUTOFF) -> dict[str, Any]:
    """Train both candidates on historical train data and evaluate on later data."""
    dataset = load_eligible_dataset(db_path)
    train, validation = split_by_t0(dataset, cutoff=cutoff)

    x_train = train[FEATURE_COLUMNS]
    y_train = (train["label"] == "POSITIVE").astype(int)
    x_validation = validation[FEATURE_COLUMNS]
    y_validation = (validation["label"] == "POSITIVE").astype(int)

    if y_train.nunique() < 2:
        raise DatasetError("Training set must contain both POSITIVE and NEGATIVE labels")

    results: dict[str, Any] = {
        "cutoff": cutoff,
        "eligible_rows": int(len(dataset)),
        "train": asdict(summarize_split(train)),
        "validation": asdict(summarize_split(validation)),
        "features": FEATURE_COLUMNS,
        "models": {},
        "versions": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "random_state": RANDOM_STATE,
        "preprocessing_fit_policy": "Each pipeline fits preprocessing on X_train only.",
        "sealed_later_test_used": False,
    }

    for name, pipeline in make_model_pipelines().items():
        # Pipeline.fit fits the imputer/scaler/encoder on X_train only.
        pipeline.fit(x_train, y_train)
        results["models"][name] = evaluate_model(
            pipeline, x_validation, y_validation
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PLPT-52 chronological candidate model training"
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parents[2] / "data" / "songnhat.db"),
        help="Path to the existing SQLite database (read-only)",
    )
    parser.add_argument("--cutoff", default=CUTOFF)
    args = parser.parse_args()

    results = run_training(args.db, cutoff=args.cutoff)
    print("[PLPT-52] TRAINING AND VALIDATION COMPLETE")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
