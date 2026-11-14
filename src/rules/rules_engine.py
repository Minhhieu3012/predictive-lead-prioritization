"""PLPT-16: Deterministic Rules Baseline Queue Engine."""

from datetime import datetime, date
from typing import Any

RULES_LABEL = "RULES_BASELINE"

# Tổng trọng số: 100 điểm
WEIGHTS = {
    "product_fit": 30,
    "spec_completeness": 25,
    "enquiry_age": 20,
    "sla_urgency": 25,
}

SUPPORTED_PRODUCTS = {
    "Motor_DongCo",
    "Drive_BienTan",
    "Transmission_HopSo",
    "Industrial Pump",
    "Packaging Line",
    "Spare Parts",
}

CLOSED_WORKFLOW_STATES = {"closed", "withdrawn"}


def _first_value(enquiry: dict, *fields):
    for field in fields:
        value = enquiry.get(field)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _parse_datetime(value: Any):
    if value is None or str(value).strip() == "":
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return None


def _parse_date(value: Any):
    if value is None or str(value).strip() == "":
        return None

    parsed_datetime = _parse_datetime(value)
    if parsed_datetime:
        return parsed_datetime.date()

    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _days_between(start, end):
    if start.tzinfo and end.tzinfo:
        return (end - start).total_seconds() / 86400

    return (
        end.replace(tzinfo=None) -
        start.replace(tzinfo=None)
    ).total_seconds() / 86400


def _is_complete(value):
    if value is None:
        return False

    normalized = str(value).strip().lower()

    return normalized in {
        "yes", "true", "1", "complete",
        "completed", "full", "100",
        "100%", "complete specifications"
    }


def _is_urgent_replacement(enquiry):
    value = _first_value(
        enquiry,
        "is_urgent_replacement",
        "urgency_flag_at_t0",
        "urgency_flag",
    )

    if value is None:
        return False

    normalized = str(value).strip().lower().replace("-", "_")

    return normalized in {
        "urgent",
        "true",
        "yes",
        "1",
        "urgent_replacement",
        "emergency_replacement",
        "critical",
    }


def _is_closed_enquiry(enquiry: dict) -> bool:
    state = _first_value(
        enquiry,
        "workflow_state",
        "current_workflow_state_export_only",
    )
    if state is None:
        return False
    return str(state).strip().lower() in CLOSED_WORKFLOW_STATES


def calculate_rules_score(enquiry: dict, now=None) -> dict:
    """Calculate deterministic RulesScore for one enquiry."""

    now = now or datetime.now()

    if isinstance(now, date) and not isinstance(now, datetime):
        now = datetime.combine(now, datetime.min.time())

    contributions = {}

    # 1. Product fit: maximum 30 points.
    product = _first_value(
        enquiry,
        "product_family",
        "product_category_at_t0",
        "product_category",
    )

    product_points = (
        WEIGHTS["product_fit"]
        if str(product or "").strip() in SUPPORTED_PRODUCTS
        else 0
    )

    contributions["product_fit"] = {
        "points": product_points,
        "reason": (
            "Supported product category"
            if product_points
            else "Missing or unsupported product category"
        ),
    }

    # 2. Specification completeness: maximum 25 points.
    completeness = _first_value(
        enquiry,
        "spec_completeness",
        "request_completeness_at_t0",
        "request_completeness",
    )

    spec_points = (
        WEIGHTS["spec_completeness"]
        if _is_complete(completeness)
        else 0
    )

    contributions["spec_completeness"] = {
        "points": spec_points,
        "reason": (
            "Technical specifications complete"
            if spec_points
            else "Technical specifications incomplete"
        ),
    }

    # 3. Enquiry age: maximum 20 points.
    t0_value = _first_value(
        enquiry,
        "t0_time",
        "feature_event_time",
    )

    t0 = _parse_datetime(t0_value)

    age_days = max(0, _days_between(t0, now)) if t0 else 0

    if age_days >= 7:
        age_points = 20
    elif age_days >= 3:
        age_points = 15
    elif age_days >= 1:
        age_points = 10
    elif t0:
        age_points = 5
    else:
        age_points = 0

    contributions["enquiry_age"] = {
        "points": age_points,
        "reason": (
            f"Enquiry age: {age_days:.1f} days"
            if t0 else "Missing enquiry timestamp"
        ),
    }

    # 4. SLA urgency: maximum 25 points.
    due_value = _first_value(
        enquiry,
        "contract_due_date",
        "sla_due_date",
        "commitment_due_date",
        "contract_deadline",
    )

    due_date = _parse_date(due_value)
    days_to_due = (due_date - now.date()).days if due_date else None

    if days_to_due is None:
        sla_points = 0
    elif days_to_due <= 1:
        sla_points = 25
    elif days_to_due <= 3:
        sla_points = 20
    elif days_to_due <= 7:
        sla_points = 12
    else:
        sla_points = 5

    contributions["sla_urgency"] = {
        "points": sla_points,
        "reason": (
            f"Contract due in {days_to_due} day(s)"
            if days_to_due is not None
            else "No contract due date"
        ),
    }

    # Tier 0: chỉ tính quá hạn SLA / khẩn cấp cho hồ sơ chưa đóng (không phải Closed/withdrawn)
    is_closed = _is_closed_enquiry(enquiry)
    urgent_replacement = _is_urgent_replacement(enquiry)
    sla_overdue = days_to_due is not None and days_to_due <= 1

    tier0 = (not is_closed) and (urgent_replacement or sla_overdue)

    total = sum(
        item["points"]
        for item in contributions.values()
    )

    # Return at most three positive contributing factors.
    top_factors = sorted(
        [
            {"factor": key, **value}
            for key, value in contributions.items()
            if value["points"] > 0
        ],
        key=lambda item: (
            -item["points"],
            item["factor"],
        ),
    )[:3]

    # Luôn trả về đúng 3 chuỗi lý do [str, str, str] theo chuẩn JSON mục 6
    all_sorted_factors = sorted(
        contributions.items(),
        key=lambda kv: (-kv[1]["points"], kv[0]),
    )
    reasons_3 = [item[1]["reason"] for item in all_sorted_factors[:3]]

    return {
        "enquiry_id": _first_value(
            enquiry, "enquiry_id", "enquiry_key", "id"
        ),
        "account_key": _first_value(enquiry, "account_key") or "",
        "rules_label": RULES_LABEL,
        "rules_score": float(total),
        "score_unit": "points",
        "priority_tier": "TIER_0" if tier0 else "STANDARD",
        "tier0_bypass": tier0,
        "top_contributors": top_factors,
        "reasons": reasons_3,
        "t0_time": t0_value or "",
        "timestamp_source": (
            "t0_time"
            if enquiry.get("t0_time")
            else "feature_event_time"
        ),
        "feature_event_time": enquiry.get("feature_event_time"),
        "feature_available_at": enquiry.get("feature_available_at"),
    }


def build_queue(enquiries: list[dict], now=None) -> list[dict]:
    """
    Priority:
    1. Tier 0 first.
    2. RulesScore descending.
    3. FIFO by t0_time.
    4. Enquiry ID as deterministic final tie-breaker.
    """

    ranked = [
        calculate_rules_score(row, now=now)
        for row in enquiries
    ]

    def sort_key(item):
        parsed_t0 = _parse_datetime(item.get("t0_time"))

        t0_key = (
            parsed_t0.timestamp()
            if parsed_t0 else float("inf")
        )

        return (
            0 if item["tier0_bypass"] else 1,
            -item["rules_score"],
            t0_key,
            str(item.get("enquiry_id") or ""),
        )

    ranked.sort(key=sort_key)

    for index, item in enumerate(ranked, start=1):
        item["queue_rank"] = index

    return ranked