"""Frozen temporal replay benchmark for the later-test cohort.

The module ranks a sealed later-test snapshot once, replaying the same
capacity K for candidate evaluation. UNKNOWN/IMMATURE outcomes are never
converted into labels; they are reported separately and used in bounds.
"""

from __future__ import annotations

from datetime import datetime
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


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
