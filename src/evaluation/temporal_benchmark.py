import hashlib
import json
from pathlib import Path
"""PLPT-28: Frozen Temporal Evaluation Benchmark."""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

MIN_SEALED_TOTAL = 200
MIN_SEALED_POS = 40
MIN_SEALED_NEG = 40
MIN_OUTCOME_COVERAGE_PCT = 90.0


class SealedCohortAlreadyUsedError(RuntimeError):
    """Ngoại lệ khi tái sử dụng tập sealed test cohort đã đánh giá trước đó."""


def _compute_cohort_fingerprint(df: pd.DataFrame, training_cutoff_t0: str) -> str:
    ids = sorted(df["enquiry_id"].astype(str).tolist()) if "enquiry_id" in df.columns else []
    payload = {"training_cutoff_t0": training_cutoff_t0, "enquiry_ids": ids}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def verify_and_lock_sealed_cohort(
    df: pd.DataFrame,
    training_cutoff_t0: str,
    lock_file: str | Path | None = None,
) -> str:
    fp = _compute_cohort_fingerprint(df, training_cutoff_t0)
    if lock_file is None:
        return fp
    lp = Path(lock_file)
    locks: dict[str, dict] = {}
    if lp.exists():
        locks = json.loads(lp.read_text(encoding="utf-8"))
    if fp in locks:
        raise SealedCohortAlreadyUsedError(
            f"Sealed test cohort {fp[:12]} đã được sử dụng tại {locks[fp].get('locked_at')}; "
            "theo quy tắc single-use không được đánh giá lặp lại."
        )
    lp.parent.mkdir(parents=True, exist_ok=True)
    locks[fp] = {
        "training_cutoff_t0": training_cutoff_t0,
        "row_count": int(len(df)),
        "locked_at": datetime.now(VN_TZ).replace(microsecond=0).isoformat(),
    }
    lp.write_text(json.dumps(locks, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp



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


def compute_average_precision(labels: np.ndarray, scores: np.ndarray) -> float:
    """Tính Average Precision (PR-AUC) trên các bản ghi đã biết nhãn (POSITIVE=1, NEGATIVE=0)."""
    if len(labels) == 0 or np.sum(labels) == 0:
        return 0.0
    order = np.argsort(-scores, kind="mergesort")
    sorted_labels = labels[order]
    tp_cumsum = np.cumsum(sorted_labels)
    ranks = np.arange(1, len(sorted_labels) + 1)
    precisions = tp_cumsum / ranks
    return float(np.sum(precisions * sorted_labels) / np.sum(sorted_labels))


def _evaluate_method_metrics(
    cohort_df: pd.DataFrame,
    score_col: str,
    capacity_k: int,
) -> dict[str, Any]:
    if cohort_df.empty:
        return {
            "selected_count": 0,
            "precision_at_k_conservative": 0.0,
            "precision_at_k_upper_bound": 0.0,
            "recall_at_k": 0.0,
            "average_precision": 0.0,
            "selected_outcome_coverage_pct": 0.0,
            "new_account_precision_at_k": 0.0,
            "returning_account_precision_at_k": 0.0,
            "selected_df": cohort_df.copy(),
        }

    # Replay theo từng ngày cohort theo giờ VN (UTC+7), tách riêng TIER_0 lên đầu (first selection only)
    df = cohort_df.copy()
    df["_cohort_date"] = [
        (_parse_iso(t).astimezone(VN_TZ).date().isoformat() if _parse_iso(t) is not None else str(t)[:10])
        for t in df["t0_time"]
    ]
    if "is_urgent_replacement" in df.columns:
        df["_is_tier0"] = df["is_urgent_replacement"].astype(str).str.strip().str.lower().isin({"urgent", "true", "1"})
    else:
        df["_is_tier0"] = False
    selected_parts = []
    seen_enq: set[str] = set()

    for _, day_df in df.groupby("_cohort_date", sort=True):
        day_df = day_df[~day_df["enquiry_id"].isin(seen_enq)].copy()
        if day_df.empty:
            continue
        day_sorted = day_df.sort_values(
            by=["_is_tier0", score_col, "t0_time", "enquiry_id"],
            ascending=[False, False, True, True],
        ).head(capacity_k)
        seen_enq.update(day_sorted["enquiry_id"].tolist())
        selected_parts.append(day_sorted)

    selected_df = pd.concat(selected_parts, ignore_index=True) if selected_parts else df.iloc[0:0].copy()
    n_sel = len(selected_df)
    if n_sel == 0:
        return {
            "selected_count": 0,
            "precision_at_k_conservative": 0.0,
            "precision_at_k_upper_bound": 0.0,
            "recall_at_k": 0.0,
            "average_precision": 0.0,
            "selected_outcome_coverage_pct": 0.0,
            "new_account_precision_at_k": 0.0,
            "returning_account_precision_at_k": 0.0,
            "selected_df": selected_df,
        }

    sel_labels = selected_df["label_norm"].to_numpy()
    sel_pos = int(np.sum(sel_labels == "POSITIVE"))
    sel_neg = int(np.sum(sel_labels == "NEGATIVE"))
    sel_unk = int(np.sum(sel_labels == "UNKNOWN"))

    prec_cons = sel_pos / n_sel
    prec_upper = (sel_pos + sel_unk) / n_sel
    sel_cov = ((sel_pos + sel_neg) / n_sel) * 100.0

    total_pos = int((df["label_norm"] == "POSITIVE").sum())
    recall_k = (sel_pos / total_pos) if total_pos > 0 else 0.0

    known_df = df[df["label_norm"].isin({"POSITIVE", "NEGATIVE"})]
    ap = compute_average_precision(
        (known_df["label_norm"] == "POSITIVE").astype(float).to_numpy(),
        pd.to_numeric(known_df[score_col], errors="coerce").fillna(0.0).to_numpy(),
    )

    prior_series = (
        pd.to_numeric(selected_df["prior_purchase_count_at_t0"], errors="coerce")
        if "prior_purchase_count_at_t0" in selected_df.columns
        else pd.Series([float("nan")] * len(selected_df), index=selected_df.index)
    )
    is_new = prior_series.isna() | (prior_series <= 0)
    new_sel = selected_df[is_new]
    ret_sel = selected_df[~is_new]
    new_prec = float((new_sel["label_norm"] == "POSITIVE").mean()) if len(new_sel) > 0 else 0.0
    ret_prec = float((ret_sel["label_norm"] == "POSITIVE").mean()) if len(ret_sel) > 0 else 0.0

    return {
        "selected_count": n_sel,
        "precision_at_k_conservative": round(prec_cons, 4),
        "precision_at_k_upper_bound": round(prec_upper, 4),
        "recall_at_k": round(recall_k, 4),
        "average_precision": round(ap, 4),
        "selected_outcome_coverage_pct": round(sel_cov, 2),
        "new_account_precision_at_k": round(new_prec, 4),
        "returning_account_precision_at_k": round(ret_prec, 4),
        "selected_df": selected_df,
    }


def evaluate_temporal_cohort(
    sealed_df: pd.DataFrame,
    *,
    training_cutoff_t0: str,
    capacity_k: int = 25,
    baseline_score_col: str = "baseline_score",
    model_score_col: str = "model_score",
    lock_file: str | Path | None = None,
) -> dict[str, Any]:
    """
    Kiểm định trên tập sealed cohort sau training_cutoff_t0:
    - Tối thiểu 200 hồ sơ, >= 40 POSITIVE, >= 40 NEGATIVE, >= 90% độ phủ nhãn trưởng thành.
    - Trả về đầy đủ metrics conservative, upper bound, Recall@K, AP, và phân tích New vs Returning.
    """
    cutoff_dt = _parse_iso(training_cutoff_t0)
    if cutoff_dt is None:
        raise ValueError(f"Invalid training_cutoff_t0: {training_cutoff_t0}")

    df = sealed_df.copy()
    valid_time_mask = []
    for t0_raw in df["t0_time"]:
        dt = _parse_iso(t0_raw)
        valid_time_mask.append(dt is not None and dt > cutoff_dt)
    df = df.loc[valid_time_mask].copy()

    df["label_norm"] = df["label"].astype(str).str.strip().str.upper()
    df.loc[~df["label_norm"].isin({"POSITIVE", "NEGATIVE"}), "label_norm"] = "UNKNOWN"

    total_eligible = int(len(df))
    verified_pos = int((df["label_norm"] == "POSITIVE").sum())
    verified_neg = int((df["label_norm"] == "NEGATIVE").sum())
    unknown_imm = int((df["label_norm"] == "UNKNOWN").sum())
    coverage_pct = round(((verified_pos + verified_neg) / total_eligible) * 100.0, 2) if total_eligible > 0 else 0.0

    floor_met = (
        total_eligible >= MIN_SEALED_TOTAL
        and verified_pos >= MIN_SEALED_POS
        and verified_neg >= MIN_SEALED_NEG
    )
    coverage_met = coverage_pct >= MIN_OUTCOME_COVERAGE_PCT

    base_metrics = _evaluate_method_metrics(df, baseline_score_col, capacity_k)
    model_metrics = _evaluate_method_metrics(df, model_score_col, capacity_k)

    base_p = base_metrics["precision_at_k_conservative"]
    model_p = model_metrics["precision_at_k_conservative"]
    lift = round(model_p / base_p, 4) if base_p > 0 else None

    return {
        "capacity_k": capacity_k,
        "cohort_counts": {
            "total_eligible": total_eligible,
            "verified_positive": verified_pos,
            "verified_negative": verified_neg,
            "unknown_immature": unknown_imm,
            "outcome_coverage_pct": coverage_pct,
        },
        "floor_met": floor_met,
        "coverage_gate_met": coverage_met
        and base_metrics["selected_outcome_coverage_pct"] >= MIN_OUTCOME_COVERAGE_PCT
        and model_metrics["selected_outcome_coverage_pct"] >= MIN_OUTCOME_COVERAGE_PCT,
        "sealed_cohort_fingerprint": verify_and_lock_sealed_cohort(df, training_cutoff_t0, lock_file=lock_file),
        "rules_baseline_metrics": {k: v for k, v in base_metrics.items() if k != "selected_df"},
        "candidate_model_metrics": {k: v for k, v in model_metrics.items() if k != "selected_df"},
        "segment_breakdown": {
            "new_accounts": {
                "rules_baseline_precision_at_k": base_metrics["new_account_precision_at_k"],
                "candidate_model_precision_at_k": model_metrics["new_account_precision_at_k"],
            },
            "returning_accounts": {
                "rules_baseline_precision_at_k": base_metrics["returning_account_precision_at_k"],
                "candidate_model_precision_at_k": model_metrics["returning_account_precision_at_k"],
            },
        },
        "precision_at_k_lift": lift,
        "sealed_df": df,
        "baseline_selected_df": base_metrics["selected_df"],
        "model_selected_df": model_metrics["selected_df"],
    }
