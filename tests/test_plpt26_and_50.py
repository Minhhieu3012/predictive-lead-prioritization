import json
import numpy as np
import pytest
from fastapi import HTTPException
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier

from src.api.workspace_review import (
    OverrideRequest,
    ReviewRequest,
    staff_override_logs,
    submit_workspace_override,
    submit_workspace_review,
)
from src.models.explainer import (
    NON_CAUSAL_DISCLAIMER,
    compute_object_sha256,
    export_reproducibility_manifest,
    extract_top_contributing_factors,
)


def filter_features_at_or_before_t0(records, t0_cutoff):
    forbidden_cols = {"account_key", "enquiry_id", "contact_name", "phone", "email", "free_text_note"}
    cleaned = []
    for row in records:
        if row["feature_available_at"] > t0_cutoff:
            raise ValueError(f"Data leakage detected after t0: {row['feature_available_at']} > {t0_cutoff}")
        cleaned.append({k: v for k, v in row.items() if k not in forbidden_cols})
    return cleaned


def run_production_scoring(model, X_features, source_tables):
    if "outcome_events" in source_tables:
        raise PermissionError("Architectural guard: production scoring must never read outcome_events.")
    return model.predict_proba(X_features)[:, 1]


def test_block_post_t0_feature_leakage():
    t0 = "2026-08-31T10:00:00Z"
    leaked_records = [{"feature_available_at": "2026-08-31T10:00:01Z", "spec_completeness": 0.9}]
    with pytest.raises(ValueError, match="Data leakage detected"):
        filter_features_at_or_before_t0(leaked_records, t0)


def test_production_scoring_isolates_outcome_events():
    X = np.array([[0.8, 12.0], [0.4, 3.0], [0.9, 25.0], [0.2, 1.0]])
    y = np.array([1, 0, 1, 0])
    clf = LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=1000, random_state=42).fit(X, y)

    with pytest.raises(PermissionError, match="outcome_events"):
        run_production_scoring(clf, X, source_tables=["pre_t0_features", "outcome_events"])

    scores = run_production_scoring(clf, X, source_tables=["pre_t0_features"])
    assert len(scores) == 4


def test_reproducibility_and_top3_contributing_factors(tmp_path):
    X = np.array([[0.8, 12.0, 1.0, 50.0], [0.3, 2.0, 0.0, 10.0], [0.9, 20.0, 1.0, 80.0], [0.1, 1.0, 0.0, 5.0]])
    y = np.array([1, 0, 1, 0])

    logreg = LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=1000, random_state=42).fit(X, y)
    tree = DecisionTreeClassifier(max_depth=4, min_samples_leaf=1, random_state=42).fit(X, y)

    s1 = logreg.predict_proba(X)[:, 1]
    s2 = logreg.predict_proba(X)[:, 1]
    assert np.max(np.abs(s1 - s2)) <= 1e-6

    feature_names = ["spec_completeness", "account_age_days_at_t0", "prior_purchase_count_at_t0", "estimated_value_band_at_t0"]
    raw_vals = {
        "spec_completeness": 0.8,
        "account_age_days_at_t0": 12,
        "prior_purchase_count_at_t0": 1,
        "estimated_value_band_at_t0": 50,
    }
    timestamps = {col: "2026-08-31T09:30:00Z" for col in feature_names}

    for model in (logreg, tree):
        res = extract_top_contributing_factors(model, feature_names, X[0], raw_vals, timestamps, top_k=3)
        assert len(res["contributing_factors"]) <= 3
        assert res["disclaimer"] == NON_CAUSAL_DISCLAIMER

    manifest_file = export_reproducibility_manifest(
        output_dir=tmp_path,
        model_version="plpt-logreg-v1.0.0",
        candidate_type="REGULARIZED_LOGISTIC_REGRESSION",
        random_seed=42,
        training_cutoff_t0="2026-08-31T23:59:59Z",
        mature_horizon_days=60,
        training_row_count=1020,
        feature_columns_used=feature_names,
        feature_dictionary_hash=compute_object_sha256(feature_names),
        preprocessor_hash=compute_object_sha256({"scaler": "StandardScaler"}),
    )
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert data["random_seed"] == 42
    assert len(data["feature_columns_used"]) <= 30


def test_workspace_review_and_override_endpoints():
    staff_override_logs.clear()

    # Từ chối nếu chưa xác nhận quyền liên hệ hợp pháp
    with pytest.raises(HTTPException) as exc1:
        submit_workspace_review(
            ReviewRequest(enquiry_id="SN-001", contact_permission_verified=False)
        )
    assert exc1.value.status_code == 400

    # Ghi nhận ACCEPT_PRIORITY thành công vào staff_override_logs
    res_accept = submit_workspace_review(
        ReviewRequest(
            enquiry_id="SN-001",
            contact_permission_verified=True,
            contact_attempted=True,
            review_completed=True,
            next_step_note="Confirmed 75kW motor technical datasheet",
        )
    )
    assert res_accept["status"] == "RECORDED"
    assert res_accept["data"]["target_table"] == "staff_override_logs"

    # Từ chối OVERRIDE nếu thiếu lý do (HTTP 400)
    with pytest.raises(HTTPException) as exc2:
        submit_workspace_override(
            OverrideRequest(
                enquiry_id="SN-015",
                override_reason_code="CUSTOMER_URGENT_CALLBACK",
                justification="   ",
            )
        )
    assert exc2.value.status_code == 400

    # Ghi nhận OVERRIDE_PRIORITY hợp lệ
    res_override = submit_workspace_override(
        OverrideRequest(
            enquiry_id="SN-015",
            override_reason_code="CUSTOMER_URGENT_CALLBACK",
            justification="Buyer requested immediate replacement quote due to line stoppage",
        )
    )
    assert res_override["status"] == "OVERRIDDEN"
    assert len(staff_override_logs) == 2