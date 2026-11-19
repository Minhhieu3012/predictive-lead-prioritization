"""Daily review-capacity allocation for the Sales workspace."""

from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from src.rules.rules_engine import _parse_datetime, build_queue

DEFAULT_REVIEW_CAPACITY_K = 25
MAX_SEED = (1 << 63) - 1
VALID_SCORING_MODES = {"RULES_BASELINE", "PREDICTIVE_ML"}


def configured_capacity_k() -> int:
    raw_value = os.getenv("REVIEW_CAPACITY_K", str(DEFAULT_REVIEW_CAPACITY_K))
    try:
        capacity_k = int(raw_value)
    except ValueError as error:
        raise ValueError("REVIEW_CAPACITY_K must be a non-negative integer") from error
    if capacity_k < 0:
        raise ValueError("REVIEW_CAPACITY_K must be a non-negative integer")
    return capacity_k


def rank_enquiries_by_cohort(
    enquiries: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Score and rank each cohort independently, preserving its source fields."""
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[str] = set()
    for enquiry in enquiries:
        enquiry_id = str(enquiry.get("enquiry_id") or "").strip()
        cohort = str(enquiry.get("cohort") or "").strip()
        if not enquiry_id:
            raise ValueError("Every allocatable enquiry must have an enquiry_id")
        if not cohort:
            raise ValueError(f"Enquiry {enquiry_id} has no cohort")
        if enquiry_id in seen_ids:
            raise ValueError(f"Duplicate enquiry_id in allocation input: {enquiry_id}")
        seen_ids.add(enquiry_id)
        cohorts[cohort].append(enquiry)

    ranked: list[dict[str, Any]] = []
    for cohort in sorted(cohorts):
        source_by_id = {
            str(enquiry["enquiry_id"]): enquiry for enquiry in cohorts[cohort]
        }
        cohort_scores = build_queue(
            cohorts[cohort], now=now, exclude_closed=True
        )

        def model_order_key(item: dict[str, Any]) -> tuple[float, float, str]:
            parsed_t0 = _parse_datetime(item.get("t0_time"))
            return (
                -float(item["rules_score"]),
                parsed_t0.timestamp() if parsed_t0 is not None else float("inf"),
                str(item.get("enquiry_id") or ""),
            )

        model_order = sorted(
            cohort_scores,
            key=model_order_key,
        )
        model_rank_by_id = {
            str(item["enquiry_id"]): index
            for index, item in enumerate(model_order, start=1)
        }
        for scored in cohort_scores:
            enquiry_id = str(scored["enquiry_id"])
            item = dict(source_by_id[enquiry_id])
            item.update(scored)
            item["cohort"] = cohort
            item["cohort_rank"] = int(scored["queue_rank"])
            item["model_rank"] = model_rank_by_id[enquiry_id]
            ranked.append(item)

    return sorted(ranked, key=_allocation_order)


def _allocation_order(item: dict[str, Any]) -> tuple[int, str, str]:
    try:
        cohort_rank = int(item["cohort_rank"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Each scored enquiry must have a cohort_rank") from error
    if cohort_rank < 1:
        raise ValueError("cohort_rank must be positive")

    cohort = str(item.get("cohort") or "").strip()
    enquiry_id = str(item.get("enquiry_id") or "").strip()
    if not cohort or not enquiry_id:
        raise ValueError("Each scored enquiry must have a cohort and enquiry_id")
    return cohort_rank, cohort, enquiry_id


def _seeded_exploration_selection(
    pool: list[dict[str, Any]],
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    def selection_key(item: dict[str, Any]) -> tuple[bytes, str]:
        identifier = str(item["enquiry_id"])
        cohort = str(item["cohort"])
        material = f"{seed}\0{cohort}\0{identifier}".encode("utf-8")
        return hashlib.sha256(material).digest(), identifier

    return sorted(pool, key=selection_key)[:count]


def allocate_ranked_enquiries(
    ranked_enquiries: list[dict[str, Any]],
    *,
    business_date: date | str,
    capacity_k: int = DEFAULT_REVIEW_CAPACITY_K,
    seed: int,
    carry_in_tenths: int = 0,
) -> dict[str, Any]:
    """Allocate mandatory, ranked, and seeded-exploration slots."""
    if isinstance(capacity_k, bool) or not isinstance(capacity_k, int):
        raise ValueError("capacity_k must be a non-negative integer")
    if capacity_k < 0:
        raise ValueError("capacity_k must be a non-negative integer")
    if isinstance(carry_in_tenths, bool) or not isinstance(carry_in_tenths, int):
        raise ValueError("carry_in_tenths must be a non-negative integer")
    if carry_in_tenths < 0:
        raise ValueError("carry_in_tenths must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an integer between 0 and {MAX_SEED}")

    if isinstance(business_date, date):
        business_date_text = business_date.isoformat()
    else:
        try:
            business_date_text = date.fromisoformat(business_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("business_date must be an ISO date") from error

    candidates = sorted(ranked_enquiries, key=_allocation_order)
    candidate_ids = [str(item["enquiry_id"]) for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Duplicate enquiry_id in allocation candidates")
    for item in candidates:
        if item.get("score_mode") not in VALID_SCORING_MODES:
            raise ValueError(
                f"Enquiry {item.get('enquiry_id')} has no valid scoring mode"
            )
        if item.get("score_unit") != "points":
            raise ValueError(
                f"Enquiry {item.get('enquiry_id')} score must use points"
            )
        if item.get("is_quarantined") is True or item.get("is_scored") is False:
            raise ValueError(
                f"Ineligible enquiry passed to allocation: {item.get('enquiry_id')}"
            )

    mandatory = [item for item in candidates if bool(item.get("tier0_bypass"))]
    discretionary = [item for item in candidates if not bool(item.get("tier0_bypass"))]
    mandatory_count = len(mandatory)
    capacity_exceeded_by = max(0, mandatory_count - capacity_k)

    if capacity_exceeded_by:
        return {
            "business_date": business_date_text,
            "capacity_k": capacity_k,
            "mandatory_count": mandatory_count,
            "discretionary_slots": 0,
            "ranked_slots": 0,
            "exploration_slots": 0,
            "exploration_quota_slots": 0,
            "exploration_seed": seed,
            "carry_in_tenths": carry_in_tenths,
            "carry_out_tenths": carry_in_tenths,
            "capacity_exceeded_by": capacity_exceeded_by,
            "status": "CAPACITY_EXCEEDED",
            "selected": [
                {**item, "allocation_type": "MANDATORY"} for item in mandatory
            ],
            "deferred": [
                {**item, "allocation_type": "DEFERRED"} for item in discretionary
            ],
        }

    discretionary_slots = max(0, capacity_k - mandatory_count)
    quota_tenths = carry_in_tenths + discretionary_slots
    exploration_quota_slots = min(quota_tenths // 10, discretionary_slots)
    ranked_slots = discretionary_slots - exploration_quota_slots

    ranked_selected = discretionary[:ranked_slots]
    exploration_pool = discretionary[ranked_slots:]
    exploration_count = min(exploration_quota_slots, len(exploration_pool))
    exploration_selected = _seeded_exploration_selection(
        exploration_pool, exploration_count, seed
    )
    selected_ids = {
        str(item["enquiry_id"])
        for item in ranked_selected + exploration_selected
    }

    carry_out_tenths = quota_tenths - exploration_count * 10
    selected = [
        {**item, "allocation_type": "MANDATORY"} for item in mandatory
    ]
    selected.extend(
        {**item, "allocation_type": "RANKED"} for item in ranked_selected
    )
    selected.extend(
        {**item, "allocation_type": "EXPLORATION"}
        for item in exploration_selected
    )
    deferred = [
        {**item, "allocation_type": "DEFERRED"}
        for item in discretionary
        if str(item["enquiry_id"]) not in selected_ids
    ]

    return {
        "business_date": business_date_text,
        "capacity_k": capacity_k,
        "mandatory_count": mandatory_count,
        "discretionary_slots": discretionary_slots,
        "ranked_slots": len(ranked_selected),
        "exploration_slots": exploration_count,
        "exploration_quota_slots": exploration_quota_slots,
        "exploration_seed": seed,
        "carry_in_tenths": carry_in_tenths,
        "carry_out_tenths": carry_out_tenths,
        "capacity_exceeded_by": 0,
        "status": "OK",
        "selected": selected,
        "deferred": deferred,
    }


def _ensure_allocation_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_queue_allocations (
            business_date TEXT NOT NULL,
            revision INTEGER NOT NULL,
            capacity_k INTEGER NOT NULL,
            mandatory_count INTEGER NOT NULL,
            discretionary_slots INTEGER NOT NULL,
            ranked_slots INTEGER NOT NULL,
            exploration_slots INTEGER NOT NULL,
            exploration_quota_slots INTEGER NOT NULL,
            exploration_seed INTEGER NOT NULL,
            carry_in_tenths INTEGER NOT NULL,
            carry_out_tenths INTEGER NOT NULL,
            capacity_exceeded_by INTEGER NOT NULL,
            status TEXT NOT NULL,
            selected_json TEXT NOT NULL,
            deferred_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            PRIMARY KEY (business_date, revision)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_queue_allocation_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_date TEXT NOT NULL,
            revision INTEGER NOT NULL,
            action TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            reason TEXT,
            seed INTEGER NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )


def _deserialize_allocation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "business_date": row["business_date"],
        "revision": row["revision"],
        "capacity_k": row["capacity_k"],
        "mandatory_count": row["mandatory_count"],
        "discretionary_slots": row["discretionary_slots"],
        "ranked_slots": row["ranked_slots"],
        "exploration_slots": row["exploration_slots"],
        "exploration_quota_slots": row["exploration_quota_slots"],
        "exploration_seed": row["exploration_seed"],
        "carry_in_tenths": row["carry_in_tenths"],
        "carry_out_tenths": row["carry_out_tenths"],
        "capacity_exceeded_by": row["capacity_exceeded_by"],
        "status": row["status"],
        "selected": json.loads(row["selected_json"]),
        "deferred": json.loads(row["deferred_json"]),
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


def _insert_allocation(
    conn: sqlite3.Connection,
    allocation: dict[str, Any],
    *,
    revision: int,
    actor_id: str,
    action: str,
    reason: str | None,
) -> None:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO daily_queue_allocations (
            business_date, revision, capacity_k, mandatory_count,
            discretionary_slots, ranked_slots, exploration_slots,
            exploration_quota_slots, exploration_seed, carry_in_tenths,
            carry_out_tenths, capacity_exceeded_by, status, selected_json,
            deferred_json, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            allocation["business_date"],
            revision,
            allocation["capacity_k"],
            allocation["mandatory_count"],
            allocation["discretionary_slots"],
            allocation["ranked_slots"],
            allocation["exploration_slots"],
            allocation["exploration_quota_slots"],
            allocation["exploration_seed"],
            allocation["carry_in_tenths"],
            allocation["carry_out_tenths"],
            allocation["capacity_exceeded_by"],
            allocation["status"],
            json.dumps(allocation["selected"], sort_keys=True),
            json.dumps(allocation["deferred"], sort_keys=True),
            created_at,
            actor_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO daily_queue_allocation_audit (
            business_date, revision, action, actor_id, reason, seed, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            allocation["business_date"],
            revision,
            action,
            actor_id,
            reason,
            allocation["exploration_seed"],
            created_at,
        ),
    )


def _latest_allocation(
    conn: sqlite3.Connection,
    business_date: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM daily_queue_allocations
        WHERE business_date = ?
        ORDER BY revision DESC
        LIMIT 1
        """,
        (business_date,),
    ).fetchone()


def get_or_create_daily_allocation(
    db_path: str | Path,
    ranked_enquiries: list[dict[str, Any]],
    *,
    business_date: date | str,
    capacity_k: int,
    seed: int | None = None,
) -> dict[str, Any]:
    business_date_text = (
        business_date.isoformat()
        if isinstance(business_date, date)
        else date.fromisoformat(business_date).isoformat()
    )
    with sqlite3.connect(str(db_path), timeout=30, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_allocation_tables(conn)
            existing = _latest_allocation(conn, business_date_text)
            if existing is not None:
                allocation = _deserialize_allocation(existing)
                conn.commit()
                return allocation

            previous = conn.execute(
                """
                SELECT carry_out_tenths FROM daily_queue_allocations
                WHERE business_date < ?
                ORDER BY business_date DESC, revision DESC
                LIMIT 1
                """,
                (business_date_text,),
            ).fetchone()
            carry_in_tenths = int(previous["carry_out_tenths"]) if previous else 0
            actual_seed = secrets.randbits(63) if seed is None else seed
            allocation = allocate_ranked_enquiries(
                ranked_enquiries,
                business_date=business_date_text,
                capacity_k=capacity_k,
                seed=actual_seed,
                carry_in_tenths=carry_in_tenths,
            )
            _insert_allocation(
                conn,
                allocation,
                revision=1,
                actor_id="SYSTEM",
                action="CREATED",
                reason=None,
            )
            conn.commit()
            return {**allocation, "revision": 1, "created_by": "SYSTEM"}
        except Exception:
            conn.rollback()
            raise


def regenerate_daily_allocation(
    db_path: str | Path,
    ranked_enquiries: list[dict[str, Any]],
    *,
    business_date: date | str,
    capacity_k: int,
    actor_id: str,
    reason: str,
    seed: int | None = None,
) -> dict[str, Any]:
    business_date_text = (
        business_date.isoformat()
        if isinstance(business_date, date)
        else date.fromisoformat(business_date).isoformat()
    )
    actor_id = actor_id.strip()
    reason = reason.strip()
    if not actor_id:
        raise ValueError("actor_id is required to regenerate an allocation")
    if not reason:
        raise ValueError("reason is required to regenerate an allocation")

    with sqlite3.connect(str(db_path), timeout=30, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_allocation_tables(conn)
            existing = _latest_allocation(conn, business_date_text)
            if existing is None:
                raise LookupError(
                    f"No daily allocation exists for {business_date_text}"
                )

            revision = int(existing["revision"]) + 1
            actual_seed = secrets.randbits(63) if seed is None else seed
            allocation = allocate_ranked_enquiries(
                ranked_enquiries,
                business_date=business_date_text,
                capacity_k=capacity_k,
                seed=actual_seed,
                carry_in_tenths=int(existing["carry_in_tenths"]),
            )
            _insert_allocation(
                conn,
                allocation,
                revision=revision,
                actor_id=actor_id,
                action="REGENERATED",
                reason=reason,
            )
            conn.commit()
            return {
                **allocation,
                "revision": revision,
                "created_by": actor_id,
                "regeneration_reason": reason,
            }
        except Exception:
            conn.rollback()
            raise


def get_daily_allocation(
    db_path: str | Path,
    business_date: date | str,
) -> dict[str, Any] | None:
    business_date_text = (
        business_date.isoformat()
        if isinstance(business_date, date)
        else date.fromisoformat(business_date).isoformat()
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'daily_queue_allocations'
            """
        ).fetchone()
        if table_exists is None:
            return None
        row = _latest_allocation(conn, business_date_text)
        return _deserialize_allocation(row) if row is not None else None
