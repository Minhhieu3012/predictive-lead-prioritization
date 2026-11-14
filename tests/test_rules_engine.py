from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from src.api.main import app
from src.rules.rules_engine import (
    WEIGHTS,
    RULES_LABEL,
    calculate_rules_score,
    build_queue,
)


NOW = datetime(2026, 11, 14, 12, 0, 0)


def make_enquiry(**overrides):
    enquiry = {
        "enquiry_id": "ENQ001",
        "account_key": "ACCT-001",
        "product_category_at_t0": "Industrial Pump",
        "request_completeness_at_t0": "yes",
        "urgency_flag_at_t0": "normal",
        "feature_event_time": (
            NOW - timedelta(days=8)
        ).isoformat(),
        "contract_due_date": "2026-11-14",
        "workflow_state": "Under_Review",
    }

    enquiry.update(overrides)
    return enquiry


def test_weights_total_100():
    assert sum(WEIGHTS.values()) == 100


def test_rules_score_maximum_100():
    result = calculate_rules_score(
        make_enquiry(),
        now=NOW,
    )

    assert result["rules_score"] == 100
    assert result["score_unit"] == "points"
    assert result["rules_label"] == RULES_LABEL


def test_product_fit_weight():
    result = calculate_rules_score(
        make_enquiry(),
        now=NOW,
    )

    assert (
        result["rules_score"] >= WEIGHTS["product_fit"]
    )


def test_spec_completeness_weight():
    result = calculate_rules_score(
        make_enquiry(),
        now=NOW,
    )

    factors = {
        item["factor"]: item["points"]
        for item in result["top_contributors"]
    }

    assert (
        factors["spec_completeness"]
        == WEIGHTS["spec_completeness"]
    )


def test_tier0_urgent_replacement():
    urgent = make_enquiry(
        urgency_flag_at_t0="urgent_replacement",
        contract_due_date="2026-12-30",
    )

    normal = make_enquiry(
        enquiry_id="ENQ002",
        urgency_flag_at_t0="normal",
        contract_due_date="2026-12-30",
    )

    queue = build_queue(
        [normal, urgent],
        now=NOW,
    )

    assert queue[0]["enquiry_id"] == "ENQ001"
    assert queue[0]["priority_tier"] == "TIER_0"


def test_tier0_urgent_sla():
    enquiry = make_enquiry(
        urgency_flag_at_t0="normal",
        contract_due_date="2026-11-14",
    )

    result = calculate_rules_score(enquiry, now=NOW)

    assert result["tier0_bypass"] is True
    assert result["priority_tier"] == "TIER_0"


def test_closed_enquiry_does_not_trigger_tier0():
    closed_old = make_enquiry(
        enquiry_id="ENQ_CLOSED",
        urgency_flag_at_t0="urgent_replacement",
        contract_due_date="2025-02-01",
        workflow_state="Closed",
    )
    withdrawn_old = make_enquiry(
        enquiry_id="ENQ_WITHDRAWN",
        urgency_flag_at_t0="normal",
        contract_due_date="2025-02-01",
        workflow_state="withdrawn",
    )

    assert calculate_rules_score(closed_old, now=NOW)["priority_tier"] == "STANDARD"
    assert calculate_rules_score(withdrawn_old, now=NOW)["priority_tier"] == "STANDARD"


def test_fifo_when_scores_are_equal():
    older = make_enquiry(
        enquiry_id="OLDER",
        feature_event_time="2026-11-10T08:00:00",
    )

    newer = make_enquiry(
        enquiry_id="NEWER",
        feature_event_time="2026-11-10T10:00:00",
    )

    queue = build_queue(
        [newer, older],
        now=NOW,
    )

    assert queue[0]["enquiry_id"] == "OLDER"
    assert queue[1]["enquiry_id"] == "NEWER"


def test_maximum_three_contributors():
    result = calculate_rules_score(
        make_enquiry(),
        now=NOW,
    )

    assert len(result["top_contributors"]) <= 3
    assert len(result["reasons"]) == 3


def test_score_is_not_probability():
    result = calculate_rules_score(
        make_enquiry(),
        now=NOW,
    )

    assert result["score_unit"] == "points"
    assert "probability" not in result
    assert "percentage" not in result
    assert "%" not in str(result)


def test_queue_rank_is_sequential():
    enquiries = [
        make_enquiry(enquiry_id=f"ENQ{i}")
        for i in range(1, 4)
    ]

    queue = build_queue(enquiries, now=NOW)

    assert [
        item["queue_rank"] for item in queue
    ] == [1, 2, 3]


def test_api_queue_contract():
    client = TestClient(app)
    res = client.get("/api/queue?limit=5")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] in ("OK", "HOLD_STALE_DATA")
    assert isinstance(data["source_age_hours"], float)
    assert len(data["items"]) == 5

    first = data["items"][0]
    expected_keys = {
        "enquiry_id",
        "account_key",
        "final_rank",
        "rule_score",
        "score_mode",
        "is_urgent",
        "is_blocked",
        "reasons",
        "t0_time",
    }
    assert set(first.keys()) == expected_keys
    assert first["score_mode"] == "RULES_BASELINE"
    assert first["final_rank"] == 1
    assert isinstance(first["reasons"], list)
    assert len(first["reasons"]) == 3