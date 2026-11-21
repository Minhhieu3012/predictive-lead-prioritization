"""Tests for temporal/account clustered paired bootstrap evaluation."""

import pytest

from src.evaluation.paired_bootstrap import (
    check_mature_label_coverage,
    paired_bootstrap_ci,
)


def _rows(count=40):
    rows = []
    for index in range(count):
        account = f"ACCT-{index // 4 + 1:02d}"
        block = f"2026-01-{(index // 10 + 1):02d}"
        # Candidate ranks positive rows earlier; baseline ranks them later.
        candidate = 100 - index
        baseline = abs(15 - index)
        label = "POSITIVE" if index < 20 else "NEGATIVE"
        rows.append(
            {
                "enquiry_id": f"SN-{index}",
                "account_key": account,
                "time_block": block,
                "maturity_state": "MATURE",
                "observer_coverage": "Complete",
                "label": label,
                "candidate_score": candidate,
                "baseline_score": baseline,
            }
        )
    return rows


def test_mature_label_coverage_requires_at_least_90_percent():
    rows = _rows(20)
    result = check_mature_label_coverage(rows)
    assert result == {
        "mature_count": 20,
        "total_count": 20,
        "coverage": 1.0,
        "threshold": 0.90,
        "passed": True,
    }

    rows[0]["maturity_state"] = "IMMATURE"
    result = check_mature_label_coverage(rows)
    assert result["coverage"] == pytest.approx(0.95)
    assert result["passed"] is True


def test_bootstrap_is_reproducible_and_preserves_cluster_units():
    rows = _rows()

    first = paired_bootstrap_ci(rows, k=10, n_bootstrap=500, seed=123)
    second = paired_bootstrap_ci(rows, k=10, n_bootstrap=500, seed=123)

    assert first == second
    assert first["cluster_count"] == 12
    assert first["lift_ci"][0] <= first["lift"] <= first["lift_ci"][1]
    assert first["ci_95"] is True


def test_gate_requires_mature_coverage_and_positive_lift_ci():
    rows = _rows()
    result = paired_bootstrap_ci(rows, k=10, n_bootstrap=500, seed=7)
    assert result["mature_label_coverage"]["passed"] is True
    assert result["gate_g1_passed"] is True


def test_low_maturity_coverage_is_rejected():
    rows = _rows(20)
    for row in rows[:10]:
        row["maturity_state"] = "IMMATURE"

    with pytest.raises(ValueError, match="below required"):
        paired_bootstrap_ci(rows, k=10, n_bootstrap=100, seed=7)
