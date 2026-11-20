"""PLPT-24 & PLPT-25: Shared Queue, Capacity K, 10% Exploration, Overdue 2BD & Telemetry Service."""

import csv
from datetime import date, datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import random
import contextlib
import sqlite3
from typing import Any
import unicodedata

from src.rules.rules_engine import (
    RULES_LABEL,
    _parse_date,
    _parse_datetime,
    build_queue,
)

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
STALE_HOLD_HOURS = 24.0
STALE_STATUS = "HOLD_STALE_DATA"
CANONICAL_PRODUCT_FAMILIES = ("Motor_DongCo", "Drive_BienTan", "Transmission_HopSo")
FALLBACK_MODE = "FALLBACK_RULES_BASELINE"

ALLOWED_OVERRIDE_REASON_CODES = {
    "CUSTOMER_URGENT_CALLBACK",
    "EXISTING_PROJECT_DEPENDENCY",
    "TECHNICAL_SPEC_CLARIFICATION",
    "OTHER_VERIFIED",
}

CAUSAL_DISCLAIMER = (
    "Contributing factors reflect historical qualification associations at t0, "
    "not causal proof that contacting a lead will result in a sale."
)

from src.config.vn_holidays import count_vn_business_days, is_vn_business_day


def get_daily_capacity_k() -> int:
    raw = os.getenv("DAILY_CAPACITY_K", "25").strip()
    try:
        val = int(raw)
        return val if val > 0 else 25
    except ValueError:
        return 25


def get_active_scoring_mode() -> str:
    """
    Khóa cứng chế độ phục vụ mặc định là RULES_BASELINE / FALLBACK_RULES_BASELINE
    cho tới khi dữ liệu thật đạt Gate G0 và có cờ phê duyệt Gate G1.
    """
    g1_approved = os.getenv("GATE_G1_APPROVED", "false").strip().lower() in {"1", "true", "yes"}
    requested = os.getenv("ACTIVE_SCORING_MODE", RULES_LABEL).strip().upper()
    if g1_approved and requested == "PREDICTIVE_ML":
        return "PREDICTIVE_ML"
    if requested == "PREDICTIVE_ML" and not g1_approved:
        return FALLBACK_MODE
    return RULES_LABEL


def get_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(os.getenv("DATABASE_PATH", str(project_root / "data" / "songnhat.db")))


def get_quarantine_dir() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(os.getenv("QUARANTINE_DIR", str(project_root / "data" / "quarantine")))


def strip_vietnamese_diacritics(text: str) -> str:
    if not text:
        return ""
    s = str(text).replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn").lower()





def ensure_operational_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id TEXT,
            account_key TEXT,
            reason TEXT,
            blocked_at TEXT NOT NULL,
            created_by TEXT DEFAULT 'PO_OPERATOR'
        )
        """
    )
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(blocked_accounts)").fetchall()
    }
    if "reason" not in existing_cols:
        conn.execute("ALTER TABLE blocked_accounts ADD COLUMN reason TEXT")
    if "created_by" not in existing_cols:
        conn.execute("ALTER TABLE blocked_accounts ADD COLUMN created_by TEXT DEFAULT 'PO_OPERATOR'")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            contact_permission_verified INTEGER NOT NULL DEFAULT 0,
            contact_attempted INTEGER NOT NULL DEFAULT 0,
            review_completed INTEGER NOT NULL DEFAULT 0,
            next_step_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_override_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id TEXT NOT NULL,
            override_reason_code TEXT NOT NULL,
            justification TEXT NOT NULL,
            overridden_by TEXT,
            overridden_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def count_quarantine_rows() -> int:
    quarantine_dir = get_quarantine_dir()
    unique_ids: set[str] = set()
    no_id_rows = 0
    for fname in ("quarantine_rows.csv", "quarantine_outcomes.csv"):
        fpath = quarantine_dir / fname
        if fpath.exists():
            with fpath.open("r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    enq_id = str(row.get("enquiry_id") or "").strip()
                    if enq_id:
                        unique_ids.add(enq_id)
                    elif any(str(v or "").strip() for v in row.values()):
                        no_id_rows += 1
    return len(unique_ids) + no_id_rows


def read_quarantine_records() -> list[dict[str, Any]]:
    quarantine_dir = get_quarantine_dir()
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for fname in ("quarantine_rows.csv", "quarantine_outcomes.csv"):
        fpath = quarantine_dir / fname
        if fpath.exists():
            with fpath.open("r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    enq_id = str(row.get("enquiry_id") or "").strip()
                    if enq_id and enq_id in seen_ids:
                        continue
                    if enq_id:
                        seen_ids.add(enq_id)
                    item = dict(row)
                    item["quarantine_source"] = fname
                    records.append(item)
    return records


def load_enquiries_and_meta() -> tuple[
    list[dict[str, Any]],
    set[str],
    set[str],
    float | None,
    set[str],
    set[str],
    int,
]:
    """
    Đọc clean_enquiries (hoặc fallback raw_enquiries), loại bỏ Closed/withdrawn,
    lấy blocked_accounts, reviewed_enquiries, overridden_enquiries, steward_review_count,
    và tính source_age_hours.
    """
    db_path = get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            has_clean = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='clean_enquiries'"
            ).fetchone()
            has_raw = cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_enquiries'"
            ).fetchone()

            table_name = "clean_enquiries" if has_clean else ("raw_enquiries" if has_raw else None)
            if table_name is None:
                return [], set(), set(), None, set(), set(), 0

            cursor.execute(
                f"SELECT * FROM {table_name} "
                f"WHERE COALESCE(LOWER(TRIM(workflow_state)), '') NOT IN ('closed', 'withdrawn')"
            )
            enquiries = [dict(row) for row in cursor.fetchall()]

            blocked_enquiries: set[str] = set()
            blocked_accounts: set[str] = set()
            if cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='blocked_accounts'"
            ).fetchone():
                for row in cursor.execute("SELECT * FROM blocked_accounts").fetchall():
                    row_dict = dict(row)
                    enq_val = str(row_dict.get("enquiry_id") or "").strip()
                    acct_val = str(row_dict.get("account_key") or "").strip()
                    if enq_val:
                        blocked_enquiries.add(enq_val)
                    elif acct_val:
                        blocked_accounts.add(acct_val)

            reviewed_enquiries: set[str] = set()
            if cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='staff_review_logs'"
            ).fetchone():
                for row in cursor.execute(
                    "SELECT DISTINCT enquiry_id FROM staff_review_logs WHERE review_completed = 1"
                ).fetchall():
                    reviewed_enquiries.add(str(row["enquiry_id"]).strip())

            overridden_enquiries: set[str] = set()
            if cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='staff_override_logs'"
            ).fetchone():
                for row in cursor.execute("SELECT DISTINCT enquiry_id FROM staff_override_logs").fetchall():
                    overridden_enquiries.add(str(row["enquiry_id"]).strip())

            steward_count = 0
            if cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='steward_review_queue'"
            ).fetchone():
                steward_count = int(
                    cursor.execute(
                        "SELECT COUNT(*) FROM steward_review_queue WHERE status = 'DATA_STEWARD'"
                    ).fetchone()[0]
                )

            has_unverified_extraction = False
            latest_ext = None
            for row in enquiries:
                ext_dt = _parse_datetime(row.get("extraction_time"))
                if ext_dt is None:
                    has_unverified_extraction = True
                    break
                if ext_dt.tzinfo is None:
                    ext_dt = ext_dt.replace(tzinfo=VN_TZ)
                if latest_ext is None or ext_dt > latest_ext:
                    latest_ext = ext_dt

            if not enquiries or has_unverified_extraction or latest_ext is None:
                age_hours: float | None = None
            else:
                now_vn = datetime.now(VN_TZ)
                age_hours = max(0.0, round((now_vn - latest_ext).total_seconds() / 3600.0, 2))

            return (
                enquiries,
                blocked_enquiries,
                blocked_accounts,
                age_hours,
                reviewed_enquiries,
                overridden_enquiries,
                steward_count,
            )


def _format_sla_due_timestamp(raw_due: Any) -> str | None:
    if raw_due is None or str(raw_due).strip() == "":
        return None
    s = str(raw_due).strip()
    dt = _parse_datetime(s)
    if dt is not None and "T" in s:
        return dt.astimezone(VN_TZ).replace(microsecond=0).isoformat()
    d = _parse_date(s)
    if d is not None:
        return datetime(d.year, d.month, d.day, 17, 0, 0, tzinfo=VN_TZ).isoformat()
    return None


def allocate_daily_k_slots(
    ranked_entries: list[dict[str, Any]],
    capacity_k: int,
    seed: int | None = None,
    overridden_enquiries: set[str] | None = None,
    reviewed_enquiries: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, bool, int]:
    """
    PLPT-24: Tách logic chọn K slot trong ngày khỏi limit phân trang:
    - TIER_0 bắt buộc lên đầu.
    - Nếu N_mandatory >= K: lấy K hồ sơ TIER_0, is_overloaded = True; nếu có OVERRIDE_PRIORITY ngoài Top-K
      thì tạm cho phép tổng hiển thị = K + số ca override.
    - Ngược lại: K_disc = K - N_mandatory; khi K_disc >= 5 thì K_explore = round(0.10 * K_disc).
    - Nếu có OVERRIDE_PRIORITY nằm ngoài Top-K: kéo lên Active (ngay sau TIER_0) và đẩy 1 hồ sơ
      không phải TIER_0 có rule_score thấp nhất chưa review ra ngoài Top-K.
    """
    if seed is None:
        env_seed = os.getenv("EXPLORATION_SEED")
        if env_seed is not None and env_seed.strip().isdigit():
            seed = int(env_seed.strip())
        else:
            seed = int(datetime.now(VN_TZ).strftime("%Y%m%d"))

    logger.info("PLPT-24 K-slot allocation started: K=%d, seed=%d, eligible=%d", capacity_k, seed, len(ranked_entries))

    ov_set = overridden_enquiries or set()
    rev_set = reviewed_enquiries or set()

    tier0_items = [e for e in ranked_entries if e.get("tier0_bypass")]
    non_tier0_items = [e for e in ranked_entries if not e.get("tier0_bypass")]

    n_mandatory = len(tier0_items)
    if n_mandatory >= capacity_k:
        selected: list[dict[str, Any]] = []
        for item in tier0_items[:capacity_k]:
            copied = dict(item)
            copied["allocation_type"] = "TIER_0_MANDATORY"
            selected.append(copied)
        selected_ids = {str(x.get("enquiry_id") or "").strip() for x in selected}
        for item in ranked_entries:
            eid = str(item.get("enquiry_id") or "").strip()
            if eid in ov_set and eid not in selected_ids:
                copied = dict(item)
                copied["allocation_type"] = "OVERRIDE_PROMOTED"
                selected.append(copied)
                selected_ids.add(eid)
        for idx, item in enumerate(selected, start=1):
            item["final_work_order"] = idx
        return selected, 0, True, seed

    k_disc = capacity_k - n_mandatory
    k_explore = round(0.10 * k_disc) if k_disc >= 5 else 0
    k_ranked = k_disc - k_explore

    selected = []
    for item in tier0_items:
        copied = dict(item)
        copied["allocation_type"] = "TIER_0_MANDATORY"
        selected.append(copied)

    top_ranked = non_tier0_items[:k_ranked]
    remaining_pool = non_tier0_items[k_ranked:]

    for item in top_ranked:
        copied = dict(item)
        copied["allocation_type"] = "TOP_RANKED"
        selected.append(copied)

    actual_explore = min(k_explore, len(remaining_pool))
    if actual_explore > 0:
        rng = random.Random(seed)
        sampled = rng.sample(remaining_pool, actual_explore)
        for item in sampled:
            copied = dict(item)
            copied["allocation_type"] = "EXPLORATION_10PCT"
            selected.append(copied)

    # Kéo các hồ sơ OVERRIDE_PRIORITY ngoài Top-K vào Active và đẩy hồ sơ không phải TIER_0 điểm thấp nhất chưa review ra ngoài
    selected_ids = {str(x.get("enquiry_id") or "").strip() for x in selected}
    outside_overrides = [
        item for item in non_tier0_items
        if str(item.get("enquiry_id") or "").strip() in ov_set
        and str(item.get("enquiry_id") or "").strip() not in selected_ids
    ]
    for ov_item in outside_overrides:
        displace_candidates = [
            x for x in selected
            if x.get("allocation_type") != "TIER_0_MANDATORY"
            and str(x.get("enquiry_id") or "").strip() not in ov_set
            and str(x.get("enquiry_id") or "").strip() not in rev_set
        ]
        if not displace_candidates:
            displace_candidates = [
                x for x in selected
                if x.get("allocation_type") != "TIER_0_MANDATORY"
                and str(x.get("enquiry_id") or "").strip() not in ov_set
            ]
        if displace_candidates:
            victim = min(
                displace_candidates,
                key=lambda x: (float(x.get("rules_score", 0.0)), -int(x.get("final_rank") or 0)),
            )
            selected.remove(victim)
        copied = dict(ov_item)
        copied["allocation_type"] = "OVERRIDE_PROMOTED"
        selected.append(copied)

    # Cập nhật final_work_order theo TIER_0 -> OVERRIDE_PRIORITY -> các hồ sơ còn lại (giữ nguyên model_rank)
    if ov_set:
        tier0_part = [x for x in selected if x.get("allocation_type") == "TIER_0_MANDATORY"]
        ov_part = [x for x in selected if x.get("allocation_type") != "TIER_0_MANDATORY" and str(x.get("enquiry_id") or "").strip() in ov_set]
        rest_part = [x for x in selected if x.get("allocation_type") != "TIER_0_MANDATORY" and str(x.get("enquiry_id") or "").strip() not in ov_set]
        selected = tier0_part + ov_part + rest_part

    for idx, item in enumerate(selected, start=1):
        item["final_work_order"] = idx

    actual_explore_final = sum(1 for x in selected if x.get("allocation_type") == "EXPLORATION_10PCT")
    return selected, actual_explore_final, False, seed


def build_workspace_queue_payload(
    *,
    view: str = "active",
    page: int = 1,
    limit: int = 50,
    query_text: str | None = None,
    include_overflow: bool = False,
    capacity_k: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    (
        enquiries,
        blocked_enquiries,
        blocked_accounts,
        source_age_hours,
        reviewed_enquiries,
        overridden_enquiries,
        steward_count,
    ) = load_enquiries_and_meta()

    now_vn = now or datetime.now(VN_TZ)
    k = capacity_k if capacity_k is not None else get_daily_capacity_k()
    scoring_mode = get_active_scoring_mode()
    quarantine_count = count_quarantine_rows()

    is_stale = source_age_hours is None or source_age_hours > STALE_HOLD_HOURS
    status = STALE_STATUS if is_stale else "OK"
    if source_age_hours is None:
        warning = (
            "Không xác minh được độ tươi dữ liệu nguồn (tập dữ liệu rỗng hoặc thiếu/sai định dạng extraction_time). "
            "Hàng đợi tạm khóa (HOLD_STALE_DATA) để đảm bảo an toàn vận hành."
        )
    elif is_stale:
        warning = (
            f"Dữ liệu nguồn đã cũ {source_age_hours} giờ (vượt ngưỡng 24 giờ). "
            "Hàng đợi tạm khóa (HOLD_STALE_DATA) để đảm bảo an toàn vận hành."
        )
    else:
        warning = None

    cohort_id = now_vn.strftime("%Y-%m-%d")

    if view == "quarantine":
        q_records = read_quarantine_records()
        if query_text:
            needle = strip_vietnamese_diacritics(query_text)
            q_records = [
                r for r in q_records
                if needle in strip_vietnamese_diacritics(" ".join(str(v) for v in r.values()))
            ]
        start = (page - 1) * limit
        paged = q_records[start : start + limit]
        return {
            "status": status,
            "locked": is_stale,
            "warning": warning,
            "view": "quarantine",
            "cohort_id": cohort_id,
            "scoring_mode": scoring_mode,
            "snapshot_age_hours": source_age_hours,
            "capacity_k": k,
            "exploration_slots_allocated": 0,
            "exploration_seed": None,
            "overdue_2bd_count": 0,
            "is_overloaded": False,
            "quarantine_count": quarantine_count,
            "steward_review_count": steward_count,
            "total": len(q_records),
            "page": page,
            "limit": limit,
            "items": paged,
        }

    # Lọc hồ sơ bị chặn khẩn cấp
    enq_by_id: dict[str, dict[str, Any]] = {}
    unblocked_enquiries: list[dict[str, Any]] = []
    for row in enquiries:
        enq_id = str(row.get("enquiry_id") or "").strip()
        acct_key = str(row.get("account_key") or "").strip()
        if (enq_id and enq_id in blocked_enquiries) or (acct_key and acct_key in blocked_accounts):
            continue
        unblocked_enquiries.append(row)
        if enq_id:
            enq_by_id[enq_id] = row

    ranked = build_queue(unblocked_enquiries, now=now_vn, exclude_closed=True)

    # Tính overdue 2 ngày làm việc trên toàn tập active hợp lệ
    overdue_2bd_count = 0
    for entry in ranked:
        enq_id = str(entry.get("enquiry_id") or "").strip()
        t0_dt = _parse_datetime(entry.get("t0_time"))
        is_reviewed = enq_id in reviewed_enquiries
        is_overdue = (
            t0_dt is not None
            and not is_reviewed
            and count_vn_business_days(t0_dt, now_vn) > 2
        )
        entry["is_overdue_2bd"] = is_overdue
        if is_overdue:
            overdue_2bd_count += 1

    # Chốt Analyst PLPT-24: Tách rạch ròi Active và Unscored (không trùng hồ sơ giữa 2 tab)
    active_candidates: list[dict[str, Any]] = []
    unscored_only: list[dict[str, Any]] = []
    for entry in ranked:
        enq_id = str(entry.get("enquiry_id") or "").strip()
        raw_row = enq_by_id.get(enq_id, {})
        prod = str(raw_row.get("product_family") or "").strip()
        spec = str(raw_row.get("spec_completeness") or "").strip()
        is_tier0 = bool(entry.get("tier0_bypass"))
        if (prod not in CANONICAL_PRODUCT_FAMILIES or spec == "") and not is_tier0:
            unscored_only.append(entry)
        else:
            active_candidates.append(entry)

    allocated, explore_count, tier0_overloaded, used_seed = allocate_daily_k_slots(
        active_candidates,
        capacity_k=k,
        overridden_enquiries=overridden_enquiries,
        reviewed_enquiries=reviewed_enquiries,
    )
    allocated_ids = {str(e.get("enquiry_id") or "").strip() for e in allocated}
    is_overloaded = tier0_overloaded or (overdue_2bd_count > k)

    if view == "unscored":
        unscored_entries = list(unscored_only)
        for entry in active_candidates:
            enq_id = str(entry.get("enquiry_id") or "").strip()
            if enq_id in allocated_ids:
                continue
            raw_row = enq_by_id.get(enq_id, {})
            if str(raw_row.get("spec_completeness") or "").strip() == "Minimal":
                unscored_entries.append(entry)

        unscored_items = [
            _format_workspace_item(
                entry,
                enq_by_id.get(str(entry.get("enquiry_id") or "").strip(), {}),
                source_age_hours,
                scoring_mode,
                cohort_id,
                overridden_enquiries,
            )
            for entry in unscored_entries
        ]
        if query_text:
            unscored_items = _filter_by_vn_query(unscored_items, query_text)
        start = (page - 1) * limit
        return {
            "status": status,
            "locked": is_stale,
            "warning": warning,
            "view": "unscored",
            "cohort_id": cohort_id,
            "scoring_mode": scoring_mode,
            "snapshot_age_hours": source_age_hours,
            "capacity_k": k,
            "exploration_slots_allocated": 0,
            "exploration_seed": None,
            "overdue_2bd_count": overdue_2bd_count,
            "is_overloaded": is_overloaded,
            "quarantine_count": quarantine_count,
            "steward_review_count": steward_count,
            "total": len(unscored_items),
            "page": page,
            "limit": limit,
            "items": [] if is_stale else unscored_items[start : start + limit],
        }

    # view == "active": bổ sung overflow Full/Partial ngoài Top-K nếu include_overflow=True
    active_display = list(allocated)
    if include_overflow:
        next_order = len(active_display) + 1
        for entry in active_candidates:
            enq_id = str(entry.get("enquiry_id") or "").strip()
            if enq_id in allocated_ids:
                continue
            raw_row = enq_by_id.get(enq_id, {})
            if str(raw_row.get("spec_completeness") or "").strip() != "Minimal":
                copied = dict(entry)
                copied["allocation_type"] = "OVERFLOW_OUTSIDE_K"
                copied["final_work_order"] = next_order
                next_order += 1
                active_display.append(copied)

    formatted_items = [
        _format_workspace_item(
            entry,
            enq_by_id.get(str(entry.get("enquiry_id") or "").strip(), {}),
            source_age_hours,
            scoring_mode,
            cohort_id,
            overridden_enquiries,
        )
        for entry in active_display
    ]

    if query_text:
        formatted_items = _filter_by_vn_query(formatted_items, query_text)

    start = (page - 1) * limit
    paged_items = [] if is_stale else formatted_items[start : start + limit]

    return {
        "status": status,
        "locked": is_stale,
        "warning": warning,
        "view": "active",
        "cohort_id": cohort_id,
        "scoring_mode": scoring_mode,
        "snapshot_age_hours": source_age_hours,
        "capacity_k": k,
        "exploration_slots_allocated": explore_count if not is_stale else 0,
        "exploration_seed": used_seed,
        "overdue_2bd_count": overdue_2bd_count,
        "is_overloaded": is_overloaded,
        "quarantine_count": quarantine_count,
        "steward_review_count": steward_count,
        "total": len(formatted_items) if not is_stale else 0,
        "page": page,
        "limit": limit,
        "items": paged_items,
    }


def _format_workspace_item(
    entry: dict[str, Any],
    raw_row: dict[str, Any],
    source_age_hours: float | None,
    scoring_mode: str,
    cohort_id: str,
    overridden_enquiries: set[str],
) -> dict[str, Any]:
    enq_id = str(entry.get("enquiry_id") or "").strip()
    is_overridden = enq_id in overridden_enquiries
    return {
        "enquiry_id": enq_id,
        "account_key": str(entry.get("account_key") or ""),
        "product_family": str(raw_row.get("product_family") or ""),
        "spec_completeness": str(raw_row.get("spec_completeness") or ""),
        "cohort_id": cohort_id,
        "owner": str(raw_row.get("owner") or raw_row.get("assigned_owner") or "Sales Team"),
        "model_rank": int(entry.get("final_rank") or entry.get("queue_rank") or 0),
        "final_work_order": int(entry.get("final_work_order") or entry.get("final_rank") or 0),
        "snapshot_age_hours": source_age_hours,
        "scoring_mode": scoring_mode,
        "sla_due_timestamp": _format_sla_due_timestamp(raw_row.get("contract_due_date")),
        "rule_score": float(entry["rules_score"]),
        "is_urgent": bool(entry.get("tier0_bypass")),
        "is_overdue_2bd": bool(entry.get("is_overdue_2bd", False)),
        "allocation_type": entry.get("allocation_type", "TOP_RANKED"),
        "rules_override": is_overridden,
        "reasons": list(entry.get("reasons") or [])[:3],
        "contributing_factors": [
            {
                "factor": r_text,
                "source_timestamp": str(raw_row.get("feature_event_time") or entry.get("t0_time") or ""),
            }
            for r_text in list(entry.get("reasons") or [])[:3]
        ],
        "disclaimer": CAUSAL_DISCLAIMER,
        "non_causal_disclaimer": "Điểm ưu tiên chỉ mang tính hỗ trợ sắp xếp, không phải xác suất thắng đơn hay quan hệ nhân quả.",
        "t0_time": str(entry.get("t0_time") or ""),
    }


def _filter_by_vn_query(items: list[dict[str, Any]], query_text: str) -> list[dict[str, Any]]:
    needle = strip_vietnamese_diacritics(query_text)
    if not needle:
        return items
    matched = []
    for item in items:
        haystack = " ".join(
            [
                str(item.get("enquiry_id") or ""),
                str(item.get("account_key") or ""),
                str(item.get("product_family") or ""),
                " ".join(item.get("reasons") or []),
            ]
        )
        # Hỗ trợ khớp cả mã chuẩn lẫn tên tiếng Việt không dấu (vd: "dong co" khớp "Motor_DongCo")
        normalized_hay = strip_vietnamese_diacritics(haystack).replace("_", " ")
        compact_hay = normalized_hay.replace(" ", "")
        compact_needle = needle.replace(" ", "")
        if needle in normalized_hay or (compact_needle and compact_needle in compact_hay):
            matched.append(item)
    return matched




class WorkspaceActionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _assert_enquiry_actionable(enquiry_id: str) -> None:
    (
        enquiries,
        blocked_enquiries,
        blocked_accounts,
        source_age_hours,
        _rev,
        _ov,
        _stew,
    ) = load_enquiries_and_meta()
    is_stale = source_age_hours is None or source_age_hours > STALE_HOLD_HOURS
    if is_stale:
        raise WorkspaceActionError(
            423,
            "Hàng đợi đang tạm khóa (HOLD_STALE_DATA); không thể ghi nhận review/override.",
        )
    matched = [r for r in enquiries if str(r.get("enquiry_id") or "").strip() == enquiry_id]
    if not matched:
        raise WorkspaceActionError(404, f"Không tìm thấy hồ sơ hoạt động: {enquiry_id}")
    acct_key = str(matched[0].get("account_key") or "").strip()
    if enquiry_id in blocked_enquiries or (acct_key and acct_key in blocked_accounts):
        raise WorkspaceActionError(409, f"Hồ sơ {enquiry_id} đã bị chặn (blocked); từ chối thao tác.")

def record_workspace_review(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    enquiry_id = str(payload.get("enquiry_id") or "").strip()
    decision_type = str(payload.get("decision_type") or "ACCEPT_PRIORITY").strip().upper()
    if not enquiry_id:
        raise ValueError("enquiry_id là bắt buộc.")
    _assert_enquiry_actionable(enquiry_id)
    if decision_type not in {"ACCEPT_PRIORITY", "OVERRIDE_PRIORITY"}:
        raise ValueError("decision_type phải là ACCEPT_PRIORITY hoặc OVERRIDE_PRIORITY.")

    contact_permission_verified = bool(payload.get("contact_permission_verified", False))
    contact_attempted = bool(payload.get("contact_attempted", False))
    if contact_attempted and not contact_permission_verified:
        raise ValueError("Phải xác minh contact_permission_verified=true trước khi ghi nhận liên hệ.")

    review_completed = bool(payload.get("review_completed", True))
    next_step_note = str(payload.get("next_step_note") or "").strip()
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    db_path = get_db_path()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_operational_tables(conn)
            conn.execute(
                """
                INSERT INTO staff_review_logs (
                    enquiry_id, decision_type, contact_permission_verified,
                    contact_attempted, review_completed, next_step_note, reviewed_by, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    enquiry_id,
                    decision_type,
                    1 if contact_permission_verified else 0,
                    1 if contact_attempted else 0,
                    1 if review_completed else 0,
                    next_step_note,
                    user_id,
                    now_iso,
                ),
            )
            conn.commit()

    return {
        "status": "recorded",
        "enquiry_id": enquiry_id,
        "decision_type": decision_type,
        "contact_permission_verified": contact_permission_verified,
        "contact_attempted": contact_attempted,
        "review_completed": review_completed,
        "reviewed_by": user_id,
        "reviewed_at": now_iso,
    }


def record_workspace_override(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    enquiry_id = str(payload.get("enquiry_id") or "").strip()
    reason_code = str(payload.get("override_reason_code") or "").strip().upper()
    justification = str(payload.get("justification") or "").strip()

    if not enquiry_id:
        raise ValueError("enquiry_id là bắt buộc.")
    _assert_enquiry_actionable(enquiry_id)
    if reason_code not in ALLOWED_OVERRIDE_REASON_CODES:
        raise ValueError(
            f"override_reason_code không hợp lệ ({reason_code}). "
            f"Cho phép: {sorted(ALLOWED_OVERRIDE_REASON_CODES)}"
        )
    if not justification:
        raise ValueError("justification không được để trống khi ghi đè thứ tự.")

    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    db_path = get_db_path()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_operational_tables(conn)
            conn.execute(
                """
                INSERT INTO staff_override_logs (
                    enquiry_id, override_reason_code, justification, overridden_by, overridden_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (enquiry_id, reason_code, justification, user_id, now_iso),
            )
            conn.commit()

    return {
        "status": "overridden",
        "enquiry_id": enquiry_id,
        "override_reason_code": reason_code,
        "justification": justification,
        "overridden_by": user_id,
        "overridden_at": now_iso,
        "isolated_from_outcome_events": True,
    }
