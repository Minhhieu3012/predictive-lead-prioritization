"""Unit tests for PLPT-52 candidate training pipeline."""

import sqlite3

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from src.models.train_candidates import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    DatasetError,
    load_eligible_dataset,
    make_model_pipelines,
    make_preprocessor,
    split_by_t0,
)


def _feature_row(enquiry_id, category="A", numeric=1):
    row = {column: category for column in FEATURE_COLUMNS}
    for column in NUMERIC_FEATURES:
        row[column] = numeric
    row["product_family"] = category
    row["spec_completeness"] = category
    row["industry_at_t0"] = category
    row["enquiry_channel_at_t0"] = category
    row["region_at_t0"] = category
    row["estimated_value_band_at_t0"] = category
    row["account_history_status"] = category
    row["enquiry_id"] = enquiry_id
    return row


def _create_test_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE pre_t0_features (
            enquiry_id TEXT PRIMARY KEY,
            product_family TEXT,
            spec_completeness TEXT,
            is_urgent_replacement INTEGER,
            contract_days_to_due_at_t0 REAL,
            industry_at_t0 TEXT,
            enquiry_channel_at_t0 TEXT,
            region_at_t0 TEXT,
            estimated_value_band_at_t0 TEXT,
            account_age_days_at_t0 REAL,
            prior_purchase_count_at_t0 REAL,
            prior_enquiry_count REAL,
            prior_distinct_product_family_count REAL,
            account_history_status TEXT
        );
        CREATE TABLE clean_enquiries (
            enquiry_id TEXT PRIMARY KEY,
            t0_time TEXT,
            cohort TEXT
        );
        CREATE TABLE raw_outcomes (
            enquiry_id TEXT PRIMARY KEY,
            observation_window_days INTEGER,
            maturity_state TEXT,
            label TEXT
        );
        CREATE TABLE merge_audit_log (
            canonical_enquiry_id TEXT,
            merged_enquiry_id TEXT
        );
        """
    )

    rows = [
        ("T1", "2025-01-10T10:00:00+07:00", "TRAINING_CANDIDATE", 60, "MATURE", "NEGATIVE"),
        ("T2", "2025-04-20T10:00:00+07:00", "TRAINING_CANDIDATE", 60, "MATURE", "POSITIVE"),
        ("V1", "2025-05-10T10:00:00+07:00", "TRAINING_CANDIDATE", 60, "MATURE", "NEGATIVE"),
        ("V2", "2025-05-20T10:00:00+07:00", "TRAINING_CANDIDATE", 60, "MATURE", "POSITIVE"),
        ("IMM", "2025-05-21T10:00:00+07:00", "TRAINING_CANDIDATE", 1, "IMMATURE", "Unknown"),
        ("SHORT", "2025-05-22T10:00:00+07:00", "TRAINING_CANDIDATE", 30, "MATURE", "NEGATIVE"),
        ("SEALED", "2025-05-23T10:00:00+07:00", "SEALED_LATER_TEST", 60, "MATURE", "POSITIVE"),
        ("OPEN", "2025-05-24T10:00:00+07:00", "OPEN_24H", 1, "IMMATURE", "Unknown"),
        ("CONFLICT", "2025-05-25T10:00:00+07:00", "TRAINING_CANDIDATE", 60, "MATURE", "POSITIVE"),
    ]

    for index, (eid, t0, cohort, days, maturity, label) in enumerate(rows):
        feature = _feature_row(eid, category=f"cat{index}", numeric=index + 1)
        conn.execute(
            """
            INSERT INTO pre_t0_features VALUES (
                :enquiry_id, :product_family, :spec_completeness,
                :is_urgent_replacement, :contract_days_to_due_at_t0,
                :industry_at_t0, :enquiry_channel_at_t0, :region_at_t0,
                :estimated_value_band_at_t0, :account_age_days_at_t0,
                :prior_purchase_count_at_t0, :prior_enquiry_count,
                :prior_distinct_product_family_count, :account_history_status
            )
            """,
            feature,
        )
        conn.execute(
            "INSERT INTO clean_enquiries VALUES (?, ?, ?)",
            (eid, t0, cohort),
        )
        conn.execute(
            "INSERT INTO raw_outcomes VALUES (?, ?, ?, ?)",
            (eid, days, maturity, label),
        )

    # The duplicate remains in raw_outcomes but is not a clean feature row.
    # Its mature label conflicts with the canonical and excludes the canonical.
    conn.execute(
        "INSERT INTO raw_outcomes VALUES (?, ?, ?, ?)",
        ("DUP", 60, "MATURE", "NEGATIVE"),
    )
    conn.execute(
        "INSERT INTO merge_audit_log VALUES (?, ?)", ("CONFLICT", "DUP")
    )
    conn.commit()
    conn.close()


def test_filters_maturity_cohort_and_conflicting_canonical(tmp_path):
    db_path = tmp_path / "plpt52.sqlite"
    _create_test_db(db_path)

    dataset = load_eligible_dataset(db_path)
    ids = set(dataset["enquiry_id"])

    assert ids == {"T1", "T2", "V1", "V2"}
    assert "SEALED" not in ids
    assert "OPEN" not in ids
    assert "IMM" not in ids
    assert "SHORT" not in ids
    assert "CONFLICT" not in ids


def test_temporal_split_is_strictly_before_and_after_cutoff(tmp_path):
    db_path = tmp_path / "plpt52.sqlite"
    _create_test_db(db_path)
    dataset = load_eligible_dataset(db_path)

    train, validation = split_by_t0(dataset)

    assert set(train["enquiry_id"]) == {"T1", "T2"}
    assert set(validation["enquiry_id"]) == {"V1", "V2"}
    assert pd.to_datetime(train["t0_time"], utc=True).max() < pd.to_datetime(
        validation["t0_time"], utc=True
    ).min()


def test_model_factory_returns_only_two_independent_pipelines():
    models = make_model_pipelines()

    assert set(models) == {
        "regularized_logistic_regression",
        "shallow_decision_tree",
    }
    assert all(isinstance(model, Pipeline) for model in models.values())
    assert (
        models["regularized_logistic_regression"].named_steps["preprocessor"]
        is not models["shallow_decision_tree"].named_steps["preprocessor"]
    )


def test_preprocessor_uses_only_declared_feature_groups():
    preprocessor = make_preprocessor()
    transformer_names = {name for name, _, _ in preprocessor.transformers}

    assert transformer_names == {"numeric", "categorical"}
    assert set(NUMERIC_FEATURES).isdisjoint(CATEGORICAL_FEATURES)
    assert set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES) == set(FEATURE_COLUMNS)


def test_unknown_validation_category_can_be_transformed_after_train_fit():
    train = pd.DataFrame(
        {
            **{column: [1, 2, 3, 4] for column in NUMERIC_FEATURES},
            **{column: ["known", "known", "other", "other"] for column in CATEGORICAL_FEATURES},
        }
    )
    validation = pd.DataFrame(
        {
            **{column: [5] for column in NUMERIC_FEATURES},
            **{column: ["never_seen"] for column in CATEGORICAL_FEATURES},
        }
    )

    preprocessor = make_preprocessor()
    preprocessor.fit(train)
    transformed = preprocessor.transform(validation)

    assert transformed.shape[0] == 1
    assert np.isfinite(transformed.toarray() if hasattr(transformed, "toarray") else transformed).all()


def test_split_rejects_invalid_t0():
    frame = pd.DataFrame(
        {
            "enquiry_id": ["A", "B"],
            "t0_time": ["2025-01-01T00:00:00+07:00", "not-a-date"],
            "label": ["NEGATIVE", "POSITIVE"],
        }
    )
    with pytest.raises(DatasetError):
        split_by_t0(frame)


def test_split_rejects_empty_temporal_partition():
    frame = pd.DataFrame(
        {
            "enquiry_id": ["A", "B"],
            "t0_time": [
                "2025-01-01T00:00:00+07:00",
                "2025-02-01T00:00:00+07:00",
            ],
            "label": ["NEGATIVE", "POSITIVE"],
        }
    )
    with pytest.raises(DatasetError):
        split_by_t0(frame, cutoff="2025-03-01T00:00:00+07:00")
