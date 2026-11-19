import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import numpy as np


NON_CAUSAL_DISCLAIMER = (
    "Contributing factors reflect historical qualification associations at t0, "
    "not causal proof that contacting a lead will result in a sale."
)


def compute_sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def compute_object_sha256(obj: Any) -> str:
    serialized = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    return compute_sha256_bytes(serialized)


def extract_top_contributing_factors(
    model: Any,
    feature_names: Sequence[str],
    transformed_row: np.ndarray,
    raw_values: Dict[str, Any],
    feature_timestamps: Dict[str, str],
    top_k: int = 3,
) -> Dict[str, Any]:
    """
    Trích xuất tối đa 3 yếu tố đóng góp thực tế (MOD 06):
    - LogisticRegression: w_j * x'_j
    - DecisionTreeClassifier: root-to-leaf path feature deltas
    """
    max_factors = min(max(int(top_k), 1), 3)
    x_vec = np.asarray(transformed_row, dtype=float).ravel()

    if hasattr(model, "coef_"):
        weights = np.asarray(model.coef_, dtype=float).ravel()
        if weights.shape[0] != x_vec.shape[0]:
            raise ValueError("Kích thước vector đặc trưng không khớp với trọng số mô hình.")
        contributions = weights * x_vec
    elif hasattr(model, "tree_"):
        tree = model.tree_
        node_indicator = model.decision_path(x_vec.reshape(1, -1))
        node_index = node_indicator.indices[
            node_indicator.indptr[0] : node_indicator.indptr[1]
        ]
        contributions = np.zeros(x_vec.shape[0], dtype=float)
        # Xác suất lớp dương (class index 1 nếu nhị phân)
        for pos in range(len(node_index) - 1):
            node_id = node_index[pos]
            next_node_id = node_index[pos + 1]
            feat_idx = tree.feature[node_id]
            if feat_idx >= 0:
                val_curr = tree.value[node_id][0]
                val_next = tree.value[next_node_id][0]
                prob_curr = val_curr[-1] / np.sum(val_curr)
                prob_next = val_next[-1] / np.sum(val_next)
                contributions[feat_idx] += float(prob_next - prob_curr)
    else:
        raise TypeError(
            "Chỉ hỗ trợ Regularized Logistic Regression hoặc Decision Tree được duyệt."
        )

    # Gom nhóm theo tên cột gốc (nếu có OneHotEncoder dạng col__val)
    grouped: Dict[str, float] = {}
    for fname, contrib in zip(feature_names, contributions):
        base_col = fname.split("__", 1)[0] if "__" in fname else fname
        grouped[base_col] = grouped.get(base_col, 0.0) + float(contrib)

    sorted_cols = sorted(
        grouped.items(),
        key=lambda item: (-abs(item[1]), item[0]),
    )

    factors: List[Dict[str, Any]] = []
    for col_name, signed_contrib in sorted_cols:
        if len(factors) >= max_factors:
            break
        if col_name not in raw_values:
            continue
        factors.append(
            {
                "feature": col_name,
                "value": raw_values[col_name],
                "contribution": round(float(signed_contrib), 6),
                "direction": "POSITIVE" if signed_contrib >= 0 else "NEGATIVE",
                "source_timestamp": feature_timestamps.get(col_name),
            }
        )

    return {
        "contributing_factors": factors,
        "disclaimer": NON_CAUSAL_DISCLAIMER,
    }


def export_reproducibility_manifest(
    output_dir: str | Path,
    model_version: str,
    candidate_type: str,
    random_seed: int,
    training_cutoff_t0: str,
    mature_horizon_days: int,
    training_row_count: int,
    feature_columns_used: Sequence[str],
    feature_dictionary_hash: str,
    preprocessor_hash: str,
    hyperparameters: Optional[Dict[str, Any]] = None,
    numerical_tolerance: float = 1e-6,
    approval_status: str = "PENDING_GATE_G1_REVIEW",
) -> Path:
    """
    Xuất artifacts/models/<model_version>/manifest.json để tái lập điểm số (MOD 08).
    """
    if len(feature_columns_used) > 30:
        raise ValueError("Số lượng cột đặc trưng vượt quá giới hạn tối đa 30 cột (DAT 06).")

    target_dir = Path(output_dir) / model_version
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / "manifest.json"

    manifest_payload = {
        "model_version": model_version,
        "candidate_type": candidate_type,
        "random_seed": int(random_seed),
        "training_cutoff_t0": training_cutoff_t0,
        "mature_horizon_days": int(mature_horizon_days),
        "training_row_count": int(training_row_count),
        "feature_columns_used": list(feature_columns_used),
        "feature_dictionary_hash": feature_dictionary_hash,
        "preprocessor_hash": preprocessor_hash,
        "hyperparameters": hyperparameters or {},
        "numerical_tolerance": float(numerical_tolerance),
        "approval_status": approval_status,
    }

    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path