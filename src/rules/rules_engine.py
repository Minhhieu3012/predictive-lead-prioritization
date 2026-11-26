"""PLPT-16: Deterministic Rules Baseline Queue Engine."""

from datetime import date, datetime, timedelta, timezone
from typing import Any

RULES_LABEL = "RULES_BASELINE"
VN_TZ = timezone(timedelta(hours=7))

# Tổng trọng số: 100 điểm (theo docs/discovery/us_02_tieu_chi_rubric_va_trang_thai.md)
WEIGHTS = {
    "product_fit": 30,
    "spec_completeness": 25,
    "enquiry_age": 20,
    "sla_urgency": 25,
}

PRODUCT_SCORES = {
    "Motor_DongCo": 30,
    "Drive_BienTan": 30,
    "Transmission_HopSo": 30,
    "Industrial Pump": 30,
    "Packaging Line": 30,
    "Spare Parts": 30,
}

SPEC_SCORES = {
    "Full": 25,
    "Partial": 15,
    "Minimal": 5,
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
        return 5, "Thong so ky thuat: Minimal (+5d)"
    s = str(raw_val).strip()
    if s in SPEC_SCORES:
        pts = SPEC_SCORES[s]
        return pts, f"Thong so ky thuat: {s} (+{pts}d)"
    low = s.lower()
    if low in {"yes", "true", "1", "complete", "completed", "full", "100", "100%", "complete specifications"}:
        return 25, "Thong so ky thuat: Full (+25d)"
    if low in {"partial", "medium", "50%"}:
        return 15, "Thong so ky thuat: Partial (+15d)"
    return 5, "Thong so ky thuat: Minimal (+5d)"


def _score_enquiry_age(enquiry: dict[str, Any], now_dt: datetime) -> tuple[int, str]:
    t0_dt = _parse_datetime(_first_value(enquiry, "t0_time", "feature_event_time"))
    if t0_dt is None:
        return 5, "Tuoi ho so: < 1 ngay (+5d)"
    age_days = (now_dt - t0_dt).total_seconds() / 86400.0
    if age_days >= 7.0:
        return 20, f"Tuoi ho so: >= 7 ngay ({int(age_days)} ngay) (+20d)"
    if age_days >= 3.0:
        return 15, f"Tuoi ho so: >= 3 ngay ({int(age_days)} ngay) (+15d)"
    if age_days >= 1.0:
        return 10, f"Tuoi ho so: >= 1 ngay ({int(age_days)} ngay) (+10d)"
    return 5, "Tuoi ho so: < 1 ngay (+5d)"


def _score_sla_urgency(enquiry: dict[str, Any], now_dt: datetime) -> tuple[int, str]:
    due_date = _parse_date(
        _first_value(enquiry, "contract_due_date", "sla_due_date", "commitment_due_date")
    )
    if due_date is None:
        return 5, "Han hop dong: > 7 ngay / chua co han (+5d)"
    days_left = (due_date - now_dt.date()).days
    if days_left <= 1:
        return 25, f"Han hop dong rat gap ({days_left} ngay) (+25d)"
    if days_left <= 3:
        return 20, f"Han hop dong gap ({days_left} ngay) (+20d)"
    if days_left <= 7:
        return 12, f"Han hop dong sap toi ({days_left} ngay) (+12d)"
    return 5, f"Han hop dong: > 7 ngay ({days_left} ngay) (+5d)"


def is_tier0_urgent(enquiry: dict[str, Any], now: datetime | None = None) -> tuple[bool, list[str]]:
    """
    Kiểm tra điều kiện ưu tiên khẩn cấp Tier 0:
    - Bản ghi có workflow_state == 'Closed' hoặc 'withdrawn' không được nâng lên Tier 0.
    - is_urgent_replacement == 'Urgent' (hoặc tương đương), HOẶC
    - contract_due_date còn <= 7 ngày tính từ now (bao gồm cả trường hợp quá hạn days_left < 0).
    """
    state = str(enquiry.get("workflow_state") or "").strip()
    if state.lower() in {"closed", "withdrawn"}:
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
    ref_dt = now or datetime.now(VN_TZ)
    ref_date = ref_dt.date() if isinstance(ref_dt, datetime) else ref_dt
    if due_date is not None and ref_date is not None:
        days_left = (due_date - ref_date).days
        if days_left <= 7:
            urgent_reasons.append(f"Han hop dong gap ({days_left} ngay)")

    return (len(urgent_reasons) > 0), urgent_reasons


def calculate_rules_score(enquiry: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Tính điểm RulesScore tất định (0..100) theo 4 tiêu chí us_02 và danh sách tối đa 3 lý do chính."""
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
    age_pts, age_reason = _score_enquiry_age(enquiry, now_dt)
    sla_pts, sla_reason = _score_sla_urgency(enquiry, now_dt)

    contributions = {
        "product_fit": {"points": prod_pts, "reason": prod_reason},
        "spec_completeness": {"points": spec_pts, "reason": spec_reason},
        "enquiry_age": {"points": age_pts, "reason": age_reason},
        "sla_urgency": {"points": sla_pts, "reason": sla_reason},
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
    1. Lọc bỏ các bản ghi đã đóng/rút (workflow_state == 'Closed' hoặc 'withdrawn') khi exclude_closed=True.
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
            and str(row.get("workflow_state") or "").strip().lower() in {"closed", "withdrawn"}
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

