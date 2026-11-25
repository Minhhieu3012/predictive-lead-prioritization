"""Frozen temporal replay benchmark for the later-test cohort.

The module ranks a sealed later-test snapshot once, replaying the same
capacity K for candidate evaluation. UNKNOWN/IMMATURE outcomes are never
converted into labels; they are reported separately and used in bounds.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

MIN_SEALED_TOTAL = 200
MIN_SEALED_POS = 40
MIN_SEALED_NEG = 40
MIN_OUTCOME_COVERAGE_PCT = 90.0


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _text(row: Mapping[str, Any], *names: str) -> str:
    value = _value(row, *names)
    return "" if value is None else str(value).strip()


def _label(row: Mapping[str, Any], label_key: str) -> str:
    value = _text(row, label_key, "label", "target", "y_true").upper()
    if value in {"1", "TRUE", "YES", "POSITIVE", "Y"}:
        return "POSITIVE"
    if value in {"0", "FALSE", "NO", "NEGATIVE", "N"}:
        return "NEGATIVE"
    return "UNKNOWN"


def _maturity(row: Mapping[str, Any]) -> str:
    return _text(row, "maturity_state").upper()


def _score(row: Mapping[str, Any], score_key: str) -> float:
    value = _value(row, score_key, "candidate_score", "score")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    return number if number == number else float("-inf")


def _timestamp(row: Mapping[str, Any]) -> datetime:
    value = _text(row, "t0_time", "t0")
    if not value:
        return datetime.max
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    except (TypeError, ValueError):
        return datetime.max


def _is_new_account(row: Mapping[str, Any]) -> bool:
    prior_count = _value(row, "prior_enquiry_count", "prior_purchase_count_at_t0")
    try:
        if prior_count is not None and float(prior_count) <= 0:
            return True
    except (TypeError, ValueError):
        pass

    history_status = _text(row, "account_history_status").upper()
    if history_status in {"UNKNOWN", "NO_PRIOR_ENQUIRY_OBSERVED"}:
        return True

    age = _value(row, "account_age_days_at_t0")
    try:
        if age is not None and float(age) == 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _bounded_metrics(ranked: Sequence[Mapping[str, Any]], k: int,
                     label_key: str) -> dict[str, dict[str, float | int]]:
    top = ranked[:k]
    known_positives = sum(
        _label(row, label_key) == "POSITIVE" for row in ranked
        if _maturity(row) == "MATURE"
    )
    unknown_count = sum(_label(row, label_key) == "UNKNOWN" for row in ranked)

    lower = {"precision": 0.0, "recall": 0.0, "ap": 0.0}
    upper = {"precision": 0.0, "recall": 0.0, "ap": 0.0}

    lower_hits = 0
    upper_hits = 0
    lower_ap_sum = 0.0
    upper_ap_sum = 0.0

    for index, row in enumerate(top, start=1):
        label = _label(row, label_key)
        if label == "POSITIVE":
            lower_hits += 1
            upper_hits += 1
        elif label == "UNKNOWN":
            upper_hits += 1

        if lower_hits:
            lower_ap_sum += lower_hits / index
        if upper_hits:
            upper_ap_sum += upper_hits / index

    lower["precision"] = lower_hits / len(top) if top else 0.0
    upper["precision"] = upper_hits / len(top) if top else 0.0

    lower_denominator = known_positives
    upper_denominator = known_positives
    lower["recall"] = lower_hits / lower_denominator if lower_denominator else 0.0
    upper["recall"] = upper_hits / upper_denominator if upper_denominator else 0.0

    lower["ap"] = lower_ap_sum / lower_denominator if lower_denominator else 0.0
    upper["ap"] = upper_ap_sum / upper_denominator if upper_denominator else 0.0

    return {
        "lower": lower,
        "upper": upper,
        "bounds": {
            "unknown_count": unknown_count,
            "known_positive_count": known_positives,
            "k": len(top),
        },
    }


def replay_temporal_benchmark(
    rows: Iterable[Mapping[str, Any]],
    *,
    k: int = 25,
    score_key: str = "candidate_score",
    label_key: str = "label",
    sealed_cohort: str = "SEALED_LATER_TEST",
    require_minimum: bool = True,
) -> dict[str, Any]:
    """Replay a frozen later-test queue and return K-level metrics.

    UNKNOWN rows remain in the queue, but are excluded from strict mature
    metrics and included in optimistic/pessimistic UNKNOWN bounds.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")

    sealed = [dict(row) for row in rows if _text(row, "cohort") == sealed_cohort]
    if require_minimum and len(sealed) < 200:
        raise ValueError("SEALED_LATER_TEST benchmark must contain at least 200 rows")

    mature = [row for row in sealed if _maturity(row) == "MATURE"]
    positive = sum(_label(row, label_key) == "POSITIVE" for row in mature)
    negative = sum(_label(row, label_key) == "NEGATIVE" for row in mature)

    if require_minimum:
        if len(mature) < 200:
            raise ValueError("SEALED_LATER_TEST must contain at least 200 mature rows")
        if positive == 0 or negative == 0:
            raise ValueError("SEALED_LATER_TEST must contain POSITIVE and NEGATIVE labels")

    def ranked(subset: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        indexed = list(subset)
        return sorted(
            indexed,
            key=lambda row: (
                -_score(row, score_key),
                _timestamp(row),
                _text(row, "enquiry_id"),
            ),
        )

    overall_ranked = ranked(sealed)
    new_rows = [row for row in sealed if _is_new_account(row)]
    old_rows = [row for row in sealed if not _is_new_account(row)]

    return {
        "cohort": sealed_cohort,
        "k": k,
        "row_count": len(sealed),
        "mature_count": len(mature),
        "unknown_count": len(sealed) - len(mature),
        "natural_positive_count": positive,
        "natural_negative_count": negative,
        "natural_positive_ratio": positive / len(mature) if mature else 0.0,
        "overall": _bounded_metrics(overall_ranked, k, label_key),
        "new_accounts": _bounded_metrics(ranked(new_rows), k, label_key),
        "existing_accounts": _bounded_metrics(ranked(old_rows), k, label_key),
        "new_account_count": len(new_rows),
        "existing_account_count": len(old_rows),
        "ranked_enquiry_ids": [str(_text(row, "enquiry_id")) for row in overall_ranked[:k]],
    }


# Short alias for direct imports and scripts.
estimate_temporal_benchmark = replay_temporal_benchmark


# ==============================================================================
# PLPT-28 Temporal Evaluation Functions
# ==============================================================================


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
