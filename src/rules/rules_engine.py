"""PLPT-16: Deterministic Rules Baseline Queue Engine."""

from datetime import date, datetime, timedelta, timezone
from typing import Any

RULES_LABEL = "RULES_BASELINE"
VN_TZ = timezone(timedelta(hours=7))

# Tổng trọng số: 100 điểm
WEIGHTS = {
    "product_fit": 30,
    "spec_completeness": 25,
    "value_band": 20,
    "account_relationship": 15,
    "channel_source": 10,
}

PRODUCT_SCORES = {
    "Motor_DongCo": 30,
    "Drive_BienTan": 28,
    "Transmission_HopSo": 25,
    "Industrial Pump": 30,
    "Packaging Line": 28,
    "Spare Parts": 25,
}

SPEC_SCORES = {
    "Full": 25,
    "Partial": 12,
    "Minimal": 0,
}

VALUE_BAND_SCORES = {
    "High": 20,
    "Medium": 12,
    "Low": 5,
}


def _first_value(enquiry: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        value = enquiry.get(field)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    parsed_dt = _parse_datetime(value)
    if parsed_dt is not None:
        return parsed_dt.date()
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def _score_spec_completeness(raw_val: Any) -> tuple[int, str]:
    if raw_val is None:
        return 0, "Thong so ky thuat: Minimal (+0d)"
    s = str(raw_val).strip()
    if s in SPEC_SCORES:
        pts = SPEC_SCORES[s]
        return pts, f"Thong so ky thuat: {s} (+{pts}d)"
    low = s.lower()
    if low in {"yes", "true", "1", "complete", "completed", "full", "100", "100%", "complete specifications"}:
        return 25, "Thong so ky thuat: Full (+25d)"
    if low in {"partial", "medium", "50%"}:
        return 12, "Thong so ky thuat: Partial (+12d)"
    return 0, "Thong so ky thuat: Minimal (+0d)"


def _score_value_band(enquiry: dict[str, Any]) -> tuple[int, str]:
    band = str(_first_value(enquiry, "estimated_value_band_at_t0", "value_band") or "").strip()
    if band in VALUE_BAND_SCORES:
        pts = VALUE_BAND_SCORES[band]
        return pts, f"Gia tri uoc tinh: {band} (+{pts}d)"
    if band.lower() == "high":
        return 20, "Gia tri uoc tinh: High (+20d)"
    if band.lower() == "medium":
        return 12, "Gia tri uoc tinh: Medium (+12d)"
    if band.lower() == "low":
        return 5, "Gia tri uoc tinh: Low (+5d)"
    return 0, "Gia tri uoc tinh: Unknown (+0d)"


def _score_account_relationship(enquiry: dict[str, Any]) -> tuple[int, str]:
    try:
        prior = int(enquiry.get("prior_purchase_count_at_t0") or 0)
    except (ValueError, TypeError):
        prior = 0
    try:
        age_days = int(enquiry.get("account_age_days_at_t0") or 0)
    except (ValueError, TypeError):
        age_days = 0

    if prior >= 3 or age_days >= 365:
        return 15, f"Khach hang than thiet (mua {prior} lan, {age_days} ngay) (+15d)"
    if prior >= 1 or age_days >= 90:
        return 10, f"Khach hang hien huu (mua {prior} lan, {age_days} ngay) (+10d)"
    return 5, "Khach hang moi (+5d)"


def _score_channel_source(enquiry: dict[str, Any]) -> tuple[int, str]:
    channel = str(_first_value(enquiry, "enquiry_channel_at_t0", "source") or "").strip()
    if channel in {"Direct_Sales", "Referral", "Phone", "CRM"}:
        return 10, f"Kenh tiep nhan uu tien: {channel} (+10d)"
    if channel in {"Email", "Zalo", "Website", "Web_Form"}:
        return 8, f"Kenh tiep nhan tieu chuan: {channel} (+8d)"
    if channel:
        return 6, f"Kenh tiep nhan: {channel} (+6d)"
    return 5, "Kenh tiep nhan mac dinh (+5d)"


def is_tier0_urgent(enquiry: dict[str, Any], now: datetime | None = None) -> tuple[bool, list[str]]:
    """
    Kiểm tra điều kiện ưu tiên khẩn cấp Tier 0:
    - Bản ghi có workflow_state == 'Closed' không được nâng lên Tier 0.
    - is_urgent_replacement == 'Urgent' (hoặc tương đương), HOẶC
    - contract_due_date nằm trong vòng 0..7 ngày tính từ t0_time (hoặc now).
    """
    state = str(enquiry.get("workflow_state") or "").strip()
    if state.lower() == "closed":
        return False, []

    urgent_reasons: list[str] = []
    urg_raw = str(
        _first_value(enquiry, "is_urgent_replacement", "urgency_flag_at_t0", "urgency_flag") or ""
    ).strip().lower().replace("-", "_")

    if urg_raw in {"urgent", "true", "yes", "1", "urgent_replacement", "emergency_replacement", "critical"}:
        urgent_reasons.append("Thay the khan cap (Urgent Replacement)")

    due_date = _parse_date(
        _first_value(enquiry, "contract_due_date", "sla_due_date", "commitment_due_date")
    )
    ref_dt = _parse_datetime(_first_value(enquiry, "t0_time", "feature_event_time")) or now
    if due_date is not None and ref_dt is not None:
        days_left = (due_date - ref_dt.date()).days
        if 0 <= days_left <= 7:
            urgent_reasons.append(f"Han hop dong gap ({days_left} ngay)")

    return (len(urgent_reasons) > 0), urgent_reasons


def calculate_rules_score(enquiry: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Tính điểm RulesScore tất định (0..100) và danh sách tối đa 3 lý do chính."""
    now_dt = now or datetime.now(VN_TZ)
    if isinstance(now_dt, date) and not isinstance(now_dt, datetime):
        now_dt = datetime.combine(now_dt, datetime.min.time(), tzinfo=VN_TZ)

    product = str(
        _first_value(enquiry, "product_family", "product_category_at_t0", "product_category") or ""
    ).strip()
    prod_pts = PRODUCT_SCORES.get(product, 0)
    prod_reason = (
        f"Nhom san pham chu luc: {product} (+{prod_pts}d)"
        if prod_pts > 0
        else "Nhom san pham ngoai danh muc (+0d)"
    )

    spec_pts, spec_reason = _score_spec_completeness(
        _first_value(enquiry, "spec_completeness", "request_completeness_at_t0", "request_completeness")
    )
    val_pts, val_reason = _score_value_band(enquiry)
    rel_pts, rel_reason = _score_account_relationship(enquiry)
    chan_pts, chan_reason = _score_channel_source(enquiry)

    contributions = {
        "product_fit": {"points": prod_pts, "reason": prod_reason},
        "spec_completeness": {"points": spec_pts, "reason": spec_reason},
        "value_band": {"points": val_pts, "reason": val_reason},
        "account_relationship": {"points": rel_pts, "reason": rel_reason},
        "channel_source": {"points": chan_pts, "reason": chan_reason},
    }

    total_score = min(100, sum(item["points"] for item in contributions.values()))
    tier0_flag, tier0_reasons = is_tier0_urgent(enquiry, now=now_dt)

    top_contributors = sorted(
        [
            {"factor": key, **value}
            for key, value in contributions.items()
            if value["points"] > 0
        ],
        key=lambda item: (-item["points"], item["factor"]),
    )[:3]

    reasons: list[str] = []
    for r in tier0_reasons:
        if len(reasons) < 3:
            reasons.append(f"[TIER 0] {r}")
    for item in top_contributors:
        if len(reasons) < 3:
            reasons.append(item["reason"])
    if not reasons:
        reasons.append("Yeu cau bao gia tieu chuan")

    t0_val = _first_value(enquiry, "t0_time", "feature_event_time")

    return {
        "enquiry_id": _first_value(enquiry, "enquiry_id", "enquiry_key", "id"),
        "account_key": enquiry.get("account_key"),
        "workflow_state": enquiry.get("workflow_state"),
        "rules_label": RULES_LABEL,
        "score_mode": RULES_LABEL,
        "rules_score": total_score,
        "rule_score": float(total_score),
        "score_unit": "points",
        "priority_tier": "TIER_0" if tier0_flag else "STANDARD",
        "tier0_bypass": tier0_flag,
        "is_tier0_urgent": tier0_flag,
        "is_urgent": tier0_flag,
        "top_contributors": top_contributors,
        "reasons": reasons[:3],
        "t0_time": t0_val,
        "timestamp_source": "t0_time" if enquiry.get("t0_time") else "feature_event_time",
        "feature_event_time": enquiry.get("feature_event_time"),
        "feature_available_at": enquiry.get("feature_available_at"),
    }


def rank_queue(
    enquiries: list[dict[str, Any]],
    now: datetime | None = None,
    exclude_closed: bool = True,
) -> list[dict[str, Any]]:
    """
    Xếp hạng danh sách enquiries theo thứ tự ưu tiên:
    1. Lọc bỏ các bản ghi đã đóng (workflow_state == 'Closed') khi exclude_closed=True.
    2. Ưu tiên Tier 0 (is_tier0_urgent = True) lên đầu.
    3. Điểm rules_score giảm dần.
    4. FIFO theo t0_time tăng dần (đến trước phục vụ trước).
    5. Tie-breaker tất định theo enquiry_id tăng dần.
    """
    filtered = [
        row
        for row in enquiries
        if not (
            exclude_closed
            and str(row.get("workflow_state") or "").strip().lower() == "closed"
        )
    ]

    scored = [calculate_rules_score(row, now=now) for row in filtered]

    def _sort_key(item: dict[str, Any]) -> tuple[int, float, float, str]:
        parsed_t0 = _parse_datetime(item.get("t0_time"))
        t0_ts = parsed_t0.timestamp() if parsed_t0 is not None else float("inf")
        return (
            0 if item["tier0_bypass"] else 1,
            -float(item["rules_score"]),
            t0_ts,
            str(item.get("enquiry_id") or ""),
        )

    scored.sort(key=_sort_key)

    for idx, item in enumerate(scored, start=1):
        item["queue_rank"] = idx
        item["final_rank"] = idx

    return scored


def build_queue(
    enquiries: list[dict[str, Any]],
    now: datetime | None = None,
    exclude_closed: bool = True,
) -> list[dict[str, Any]]:
    """Hàm đồng danh tương thích với main.py và bộ unit test."""
    return rank_queue(enquiries, now=now, exclude_closed=exclude_closed)