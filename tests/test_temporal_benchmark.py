"""Tests for frozen temporal replay benchmarks."""

import pytest

from src.evaluation.temporal_benchmark import replay_temporal_benchmark


def _sealed_rows(count=200, unknown_count=0):
    rows = []
    for index in range(count):
        is_unknown = index >= count - unknown_count
        maturity = "IMMATURE" if is_unknown else "MATURE"
        label = "Unknown" if is_unknown else ("POSITIVE" if index < 50 else "NEGATIVE")
        rows.append(
            {
                "enquiry_id": f"SN-{index:04d}",
                "cohort": "SEALED_LATER_TEST",
                "account_key": f"ACCT-{index % 20:03d}",
                "prior_enquiry_count": 0 if index < 20 else 2,
                "maturity_state": maturity,
                "label": label,
                "candidate_score": float(count - index),
                "t0_time": f"2026-10-{1 + (index % 28):02d}T09:00:00+07:00",
            }
        )
    return rows


def test_temporal_replay_has_natural_label_metrics_and_unknown_bounds():
    rows = _sealed_rows(200, unknown_count=20)

    result = replay_temporal_benchmark(rows, k=25, require_minimum=False)

    assert result["row_count"] == 200
    assert result["mature_count"] == 180
    assert result["unknown_count"] == 20
    assert result["natural_positive_ratio"] == pytest.approx(50 / 180)
    assert result["overall"]["lower"]["precision"] <= result["overall"]["upper"]["precision"]
    assert result["overall"]["lower"]["recall"] <= result["overall"]["upper"]["recall"]
    assert result["overall"]["bounds"]["k"] == 25


def test_metrics_are_split_by_new_and_existing_accounts():
    result = replay_temporal_benchmark(_sealed_rows(200), k=25)

    assert result["new_account_count"] == 20
    assert result["existing_account_count"] == 180
    assert "new_accounts" in result
    assert "existing_accounts" in result


def test_benchmark_requires_later_test_minimum_and_mature_labels():
    with pytest.raises(ValueError, match="at least 200"):
        replay_temporal_benchmark(_sealed_rows(199), k=25)

    rows = _sealed_rows(200)
    for row in rows:
        row["maturity_state"] = "IMMATURE"
    with pytest.raises(ValueError, match="at least 200 mature rows"):
        replay_temporal_benchmark(rows, k=25)
