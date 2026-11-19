"""PLPT-26: Supervised Candidate Model Training Pipeline (Conditional Gate G0/G1)."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.features.pre_t0_store import (
    APPROVED_FEATURE_DICTIONARY,
    build_pre_t0_features,
    get_feature_dictionary_hash,
)

VN_TZ = timezone(timedelta(hours=7))
MATURE_HORIZON_DAYS = 60
MIN_G0_TOTAL = 1000
MIN_G0_POS = 100
MIN_G0_NEG = 100


class GateG0NotMetError(RuntimeError):
    """Không đủ điều kiện Gate G0 để huấn luyện mô hình ML."""


class TabularPreprocessor:
    """
    Tiền xử lý bảng (SimpleImputer + StandardScaler + OneHotEncoder)
    chỉ fit trên tập huấn luyện để chống rò rỉ dữ liệu.
    """

    def __init__(self, feature_cols: list[str]):
        self.feature_cols = list(feature_cols)
        self.num_cols: list[str] = []
        self.cat_cols: list[str] = []
        self.num_medians: dict[str, float] = {}
        self.num_means: dict[str, float] = {}
        self.num_stds: dict[str, float] = {}
        self.cat_categories: dict[str, list[str]] = {}
        self.output_feature_names: list[str] = []
        self.raw_feature_map: list[str] = []

    def fit(self, df: pd.DataFrame) -> "TabularPreprocessor":
        self.num_cols = [
            c for c in self.feature_cols
            if APPROVED_FEATURE_DICTIONARY.get(c, {}).get("type") == "numeric"
        ]
        self.cat_cols = [
            c for c in self.feature_cols
            if APPROVED_FEATURE_DICTIONARY.get(c, {}).get("type") != "numeric"
        ]
        self.output_feature_names = []
        self.raw_feature_map = []

        for col in self.num_cols:
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            med = float(vals.median()) if len(vals) > 0 else 0.0
            filled = pd.to_numeric(df[col], errors="coerce").fillna(med).to_numpy(dtype=float)
            mean = float(np.mean(filled)) if len(filled) > 0 else 0.0
            std = float(np.std(filled)) if len(filled) > 0 else 1.0
            if std < 1e-8:
                std = 1.0
            self.num_medians[col] = med
            self.num_means[col] = mean
            self.num_stds[col] = std
            self.output_feature_names.append(col)
            self.raw_feature_map.append(col)

        for col in self.cat_cols:
            series = df[col].fillna("MISSING").astype(str).str.strip().replace("", "MISSING")
            cats = sorted(series.unique().tolist())
            self.cat_categories[col] = cats
            for cat in cats:
                self.output_feature_names.append(f"{col}={cat}")
                self.raw_feature_map.append(col)

        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        cols_arrays: list[np.ndarray] = []
        n = len(df)
        for col in self.num_cols:
            med = self.num_medians[col]
            mean = self.num_means[col]
            std = self.num_stds[col]
            arr = pd.to_numeric(df[col], errors="coerce").fillna(med).to_numpy(dtype=float)
            cols_arrays.append(((arr - mean) / std).reshape(n, 1))

        for col in self.cat_cols:
            series = df[col].fillna("MISSING").astype(str).str.strip().replace("", "MISSING")
            cats = self.cat_categories[col]
            mat = np.zeros((n, len(cats)), dtype=float)
            cat_idx = {c: i for i, c in enumerate(cats)}
            for row_i, val in enumerate(series):
                if val in cat_idx:
                    mat[row_i, cat_idx[val]] = 1.0
            cols_arrays.append(mat)

        if not cols_arrays:
            return np.zeros((n, 0), dtype=float)
        return np.hstack(cols_arrays)

    def fit_transform(self, df: pd.DataFrame) -> np.ndarray:
        return self.fit(df).transform(df)

    def hash_state(self) -> str:
        state = {
            "feature_cols": self.feature_cols,
            "num_medians": self.num_medians,
            "num_means": self.num_means,
            "num_stds": self.num_stds,
            "cat_categories": self.cat_categories,
        }
        digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()
        return f"sha256:{digest[:32]}"


class RegularizedLogisticRegressionModel:
    """L2-regularized Logistic Regression (lbfgs, C=1.0, random_state=42)."""

    def __init__(self, c_reg: float = 1.0, max_iter: int = 1000, random_state: int = 42):
        self.c_reg = c_reg
        self.max_iter = max_iter
        self.random_state = random_state
        self.coef_: np.ndarray = np.array([])
        self.intercept_: float = 0.0

    def fit(self, x: np.ndarray, y: np.ndarray) -> "RegularizedLogisticRegressionModel":
        n_samples, n_features = x.shape
        if n_samples == 0 or n_features == 0:
            self.coef_ = np.zeros(n_features, dtype=float)
            self.intercept_ = 0.0
            return self

        lam = 1.0 / max(self.c_reg, 1e-8)

        def _loss_and_grad(params: np.ndarray) -> tuple[float, np.ndarray]:
            w = params[:-1]
            b = params[-1]
            logits = np.clip(x @ w + b, -30.0, 30.0)
            probs = 1.0 / (1.0 + np.exp(-logits))
            eps = 1e-12
            loss = -np.sum(y * np.log(probs + eps) + (1.0 - y) * np.log(1.0 - probs + eps))
            loss += 0.5 * lam * np.sum(w * w)
            diff = probs - y
            grad_w = x.T @ diff + lam * w
            grad_b = np.sum(diff)
            return float(loss), np.append(grad_w, grad_b)

        init_params = np.zeros(n_features + 1, dtype=float)
        res = minimize(
            _loss_and_grad,
            init_params,
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": self.max_iter},
        )
        self.coef_ = res.x[:-1].copy()
        self.intercept_ = float(res.x[-1])
        return self

    def predict_scores(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.array([], dtype=float)
        logits = np.clip(x @ self.coef_ + self.intercept_, -30.0, 30.0)
        probs = 1.0 / (1.0 + np.exp(-logits))
        # Quy đổi về điểm thứ hạng [0..100] (không hiển thị % thắng)
        return np.round(probs * 100.0, 6)


class _TreeNode:
    def __init__(
        self,
        *,
        value: float,
        n_samples: int,
        feature_idx: int | None = None,
        threshold: float | None = None,
        left: "_TreeNode | None" = None,
        right: "_TreeNode | None" = None,
    ):
        self.value = value
        self.n_samples = n_samples
        self.feature_idx = feature_idx
        self.threshold = threshold
        self.left = left
        self.right = right


class ShallowDecisionTreeModel:
    """CART DecisionTreeClassifier (Gini, max_depth=4, min_samples_leaf=20, random_state=42)."""

    def __init__(self, max_depth: int = 4, min_samples_leaf: int = 20, random_state: int = 42):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.random_state = random_state
        self.root: _TreeNode | None = None

    @staticmethod
    def _gini(y: np.ndarray) -> float:
        n = len(y)
        if n == 0:
            return 0.0
        p = float(np.mean(y))
        return 2.0 * p * (1.0 - p)

    def _build(self, x: np.ndarray, y: np.ndarray, depth: int) -> _TreeNode:
        n_samples, n_features = x.shape
        pos_rate = float(np.mean(y)) if n_samples > 0 else 0.0
        if (
            depth >= self.max_depth
            or n_samples < 2 * self.min_samples_leaf
            or self._gini(y) == 0.0
        ):
            return _TreeNode(value=pos_rate, n_samples=n_samples)

        best_gain = 0.0
        best_feat = None
        best_thresh = None
        parent_gini = self._gini(y)

        for f_idx in range(n_features):
            col_vals = x[:, f_idx]
            uniq = np.unique(col_vals)
            if len(uniq) <= 1:
                continue
            thresholds = (uniq[:-1] + uniq[1:]) / 2.0
            for thresh in thresholds:
                left_mask = col_vals <= thresh
                n_left = int(np.sum(left_mask))
                n_right = n_samples - n_left
                if n_left < self.min_samples_leaf or n_right < self.min_samples_leaf:
                    continue
                g_left = self._gini(y[left_mask])
                g_right = self._gini(y[~left_mask])
                weighted = (n_left * g_left + n_right * g_right) / n_samples
                gain = parent_gini - weighted
                if gain > best_gain + 1e-12:
                    best_gain = gain
                    best_feat = f_idx
                    best_thresh = float(thresh)

        if best_feat is None or best_thresh is None:
            return _TreeNode(value=pos_rate, n_samples=n_samples)

        left_mask = x[:, best_feat] <= best_thresh
        return _TreeNode(
            value=pos_rate,
            n_samples=n_samples,
            feature_idx=best_feat,
            threshold=best_thresh,
            left=self._build(x[left_mask], y[left_mask], depth + 1),
            right=self._build(x[~left_mask], y[~left_mask], depth + 1),
        )

    def fit(self, x: np.ndarray, y: np.ndarray) -> "ShallowDecisionTreeModel":
        self.root = self._build(x, y, depth=0)
        return self

    def predict_one_with_path(self, row: np.ndarray) -> tuple[float, list[tuple[int, float]]]:
        node = self.root
        path_deltas: list[tuple[int, float]] = []
        while node is not None and node.feature_idx is not None and node.threshold is not None:
            parent_val = node.value
            if row[node.feature_idx] <= node.threshold:
                child = node.left
            else:
                child = node.right
            if child is None:
                break
            path_deltas.append((node.feature_idx, child.value - parent_val))
            node = child
        val = node.value if node is not None else 0.0
        return val, path_deltas

    def predict_scores(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.array([], dtype=float)
        return np.array([round(self.predict_one_with_path(row)[0] * 100.0, 6) for row in x], dtype=float)


def _parse_iso(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except ValueError:
        return None


def filter_mature_training_rows(
    enquiries_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    training_cutoff_t0: str,
) -> pd.DataFrame:
    """
    CF-05 & Spec 05:
    - Hợp nhất theo enquiry_id, chỉ giữ nhãn POSITIVE / NEGATIVE (loại bỏ UNKNOWN).
    - Chỉ giữ các bản ghi có t0_time + 60 ngày <= training_cutoff_t0 (loại bỏ chân trời chưa chín).
    """
    cutoff_dt = _parse_iso(training_cutoff_t0)
    if cutoff_dt is None:
        raise ValueError(f"Invalid training_cutoff_t0: {training_cutoff_t0}")

    merged = enquiries_df.merge(
        outcomes_df[["enquiry_id", "label"]],
        on="enquiry_id",
        how="inner",
    )
    merged["label_norm"] = merged["label"].astype(str).str.strip().str.upper()
    merged = merged[merged["label_norm"].isin({"POSITIVE", "NEGATIVE"})].copy()

    keep_mask = []
    for t0_raw in merged["t0_time"]:
        t0_dt = _parse_iso(t0_raw)
        if t0_dt is None:
            keep_mask.append(False)
        else:
            keep_mask.append((t0_dt + timedelta(days=MATURE_HORIZON_DAYS)) <= cutoff_dt)

    return merged.loc[keep_mask].copy()


def train_candidate_models(
    enquiries_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    *,
    training_cutoff_t0: str,
    model_version: str = "plpt-logreg-v1.0.0",
    artifact_dir: str | Path | None = None,
    enforce_g0_gate: bool = True,
    data_origin: str = "SYNTHETIC",
) -> dict[str, Any]:
    """
    Huấn luyện 2 mô hình ứng viên (Logistic Regression + Decision Tree) trên phân vùng lịch sử trước cutoff.
    Mặc định khóa chế độ phục vụ ở RULES_BASELINE cho đến khi dữ liệu thật qua Gate G0 & Gate G1.
    """
    train_raw = filter_mature_training_rows(enquiries_df, outcomes_df, training_cutoff_t0)
    pos_count = int((train_raw["label_norm"] == "POSITIVE").sum())
    neg_count = int((train_raw["label_norm"] == "NEGATIVE").sum())
    total_count = int(len(train_raw))

    if enforce_g0_gate and (
        total_count < MIN_G0_TOTAL or pos_count < MIN_G0_POS or neg_count < MIN_G0_NEG
    ):
        raise GateG0NotMetError(
            f"Gate G0 floor not met: total={total_count} (min {MIN_G0_TOTAL}), "
            f"pos={pos_count} (min {MIN_G0_POS}), neg={neg_count} (min {MIN_G0_NEG})."
        )

    feat_df, feature_cols = build_pre_t0_features(train_raw, strict_leakage=True)
    preprocessor = TabularPreprocessor(feature_cols)
    x_train = preprocessor.fit_transform(feat_df)
    y_train = (train_raw["label_norm"] == "POSITIVE").astype(float).to_numpy()

    logreg = RegularizedLogisticRegressionModel(c_reg=1.0, max_iter=1000, random_state=42).fit(x_train, y_train)
    dtree = ShallowDecisionTreeModel(max_depth=4, min_samples_leaf=20, random_state=42).fit(x_train, y_train)

    manifest = {
        "model_version": model_version,
        "candidate_type": "REGULARIZED_LOGISTIC_REGRESSION",
        "secondary_candidate_type": "DECISION_TREE_SHALLOW",
        "random_seed": 42,
        "training_cutoff_t0": training_cutoff_t0,
        "mature_horizon_days": MATURE_HORIZON_DAYS,
        "training_row_count": total_count,
        "positive_count": pos_count,
        "negative_count": neg_count,
        "data_origin": data_origin,
        "feature_columns_used": feature_cols,
        "feature_dictionary_hash": get_feature_dictionary_hash(feature_cols),
        "preprocessor_hash": preprocessor.hash_state(),
        "numerical_tolerance": 1e-6,
        "approval_status": "PENDING_GATE_G1_REVIEW",
        "serving_mode_locked": "RULES_BASELINE",
    }

    if artifact_dir is not None:
        out_dir = Path(artifact_dir) / model_version
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return {
        "manifest": manifest,
        "preprocessor": preprocessor,
        "logreg": logreg,
        "dtree": dtree,
        "feature_columns": feature_cols,
    }
