"""Unit tests cho PLPT-16: Deterministic Rules Baseline Queue Engine."""

from datetime import datetime, timedelta, timezone

from src.rules.rules_engine import (
    RULES_LABEL,
    WEIGHTS,
    build_queue,
    calculate_rules_score,
    is_tier0_urgent,
    rank_queue,
)

VN_TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=VN_TZ)


def make_enquiry(**overrides):
    enquiry = {
        "enquiry_id": "SN-0001",
        "account_key": "ACCT-0001",
        "product_family": "Motor_DongCo",
        "spec_completeness": "Full",
        "is_urgent_replacement": "Normal",
        "workflow_state": "New",
        "estimated_value_band_at_t0": "High",
        "account_age_days_at_t0": 400,
        "prior_purchase_count_at_t0": 5,
        "enquiry_channel_at_t0": "Direct_Sales",
        "t0_time": (NOW - timedelta(days=2)).isoformat(),
        "contract_due_date": "2026-12-15",
    }
    enquiry.update(overrides)
    return enquiry


def test_weights_total_100():
    assert sum(WEIGHTS.values()) == 100


def test_rules_score_maximum_100():
    result = calculate_rules_score(make_enquiry(), now=NOW)
    assert result["rules_score"] == 100
    assert result["score_unit"] == "points"
    assert result["rules_label"] == RULES_LABEL


def test_product_fit_weight():
    result = calculate_rules_score(make_enquiry(), now=NOW)
    assert result["rules_score"] >= WEIGHTS["product_fit"]


def test_spec_completeness_weight():
    result = calculate_rules_score(make_enquiry(), now=NOW)
    factors = {
        item["factor"]: item["points"]
        for item in result["top_contributors"]
    }
    assert factors["spec_completeness"] == WEIGHTS["spec_completeness"]


def test_tier0_urgent_replacement():
    urgent = make_enquiry(
        enquiry_id="SN-URG",
        is_urgent_replacement="Urgent",
        estimated_value_band_at_t0="Low",
        contract_due_date="2026-12-30",
    )
    normal = make_enquiry(
        enquiry_id="SN-NORM",
        is_urgent_replacement="Normal",
        contract_due_date="2026-12-30",
    )
    queue = build_queue([normal, urgent], now=NOW)
    assert queue[0]["enquiry_id"] == "SN-URG"
    assert queue[0]["priority_tier"] == "TIER_0"
    assert queue[0]["tier0_bypass"] is True


def test_tier0_urgent_sla_within_7_days():
    enquiry = make_enquiry(
        is_urgent_replacement="Normal",
        t0_time="2026-11-10T08:00:00+07:00",
        contract_due_date="2026-11-15",
    )
    result = calculate_rules_score(enquiry, now=NOW)
    assert result["tier0_bypass"] is True
    assert result["priority_tier"] == "TIER_0"


def test_closed_enquiry_not_tier0_and_excluded_from_queue():
    closed_urgent = make_enquiry(
        enquiry_id="SN-CLOSED",
        workflow_state="Closed",
        is_urgent_replacement="Urgent",
    )
    open_normal = make_enquiry(
        enquiry_id="SN-OPEN",
        workflow_state="New",
        is_urgent_replacement="Normal",
    )
    flag, _ = is_tier0_urgent(closed_urgent, now=NOW)
    assert flag is False

    queue = rank_queue([closed_urgent, open_normal], now=NOW)
    assert len(queue) == 1
    assert queue[0]["enquiry_id"] == "SN-OPEN"
    assert queue[0]["queue_rank"] == 1


def test_fifo_when_scores_are_equal():
    older = make_enquiry(
        enquiry_id="SN-OLDER",
        t0_time="2026-11-10T08:00:00+07:00",
    )
    newer = make_enquiry(
        enquiry_id="SN-NEWER",
        t0_time="2026-11-10T10:00:00+07:00",
    )
    queue = build_queue([newer, older], now=NOW)
    assert queue[0]["enquiry_id"] == "SN-OLDER"
    assert queue[1]["enquiry_id"] == "SN-NEWER"


def test_maximum_three_contributors_and_reasons():
    result = calculate_rules_score(make_enquiry(), now=NOW)
    assert len(result["top_contributors"]) <= 3
    assert 1 <= len(result["reasons"]) <= 3


def test_score_is_not_probability():
    result = calculate_rules_score(make_enquiry(), now=NOW)
    assert result["score_unit"] == "points"
    assert "probability" not in result
    assert "percentage" not in result


def test_queue_rank_is_sequential():
    enquiries = [make_enquiry(enquiry_id=f"SN-000{i}") for i in range(1, 4)]
    queue = build_queue(enquiries, now=NOW)
    assert [item["queue_rank"] for item in queue] == [1, 2, 3]