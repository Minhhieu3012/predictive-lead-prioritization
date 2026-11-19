from datetime import datetime, timedelta, timezone
import sqlite3

from src.queue_allocation import (
    allocate_ranked_enquiries,
    get_or_create_daily_allocation,
    rank_enquiries_by_cohort,
    regenerate_daily_allocation,
)

VN_TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=VN_TZ)


def _ranked_items(count, *, mandatory_ids=(), cohort="A"):
    mandatory_ids = set(mandatory_ids)
    return [
        {
            "enquiry_id": f"SN-{index:03d}",
            "cohort": cohort,
            "cohort_rank": index,
            "score_mode": "RULES_BASELINE",
            "score_unit": "points",
            "rules_score": 100 - index,
            "tier0_bypass": f"SN-{index:03d}" in mandatory_ids,
            "is_quarantined": False,
            "is_scored": True,
        }
        for index in range(1, count + 1)
    ]


def test_capacity_uses_90_10_slots_and_preserves_cohort_rank():
    items = _ranked_items(10)

    allocation = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=10,
        seed=7,
    )

    assert allocation["status"] == "OK"
    assert allocation["discretionary_slots"] == 10
    assert allocation["ranked_slots"] == 9
    assert allocation["exploration_slots"] == 1
    assert [item["enquiry_id"] for item in allocation["selected"][:9]] == [
        f"SN-{index:03d}" for index in range(1, 10)
    ]
    exploration = [
        item for item in allocation["selected"]
        if item["allocation_type"] == "EXPLORATION"
    ]
    assert len(exploration) == 1
    assert exploration[0]["cohort_rank"] == 10


def test_mandatory_overflow_never_drops_mandatory_work():
    items = _ranked_items(4, mandatory_ids={"SN-001", "SN-002", "SN-003"})

    allocation = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=2,
        seed=3,
    )

    assert allocation["status"] == "CAPACITY_EXCEEDED"
    assert allocation["capacity_exceeded_by"] == 1
    assert allocation["discretionary_slots"] == 0
    assert allocation["exploration_slots"] == 0
    assert {
        item["enquiry_id"] for item in allocation["selected"]
    } == {"SN-001", "SN-002", "SN-003"}
    assert {item["enquiry_id"] for item in allocation["deferred"]} == {"SN-004"}


def test_fractional_exploration_quota_carries_between_days():
    items = _ranked_items(5)
    day_one = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=5,
        seed=1,
    )
    day_two = allocate_ranked_enquiries(
        items,
        business_date="2026-11-15",
        capacity_k=5,
        seed=1,
        carry_in_tenths=day_one["carry_out_tenths"],
    )

    assert day_one["exploration_slots"] == 0
    assert day_one["carry_out_tenths"] == 5
    assert day_two["exploration_slots"] == 1
    assert day_two["carry_out_tenths"] == 0


def test_seed_reproduces_exploration_selection():
    items = _ranked_items(20)

    first = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=10,
        seed=42,
    )
    second = allocate_ranked_enquiries(
        items,
        business_date="2026-11-14",
        capacity_k=10,
        seed=42,
    )

    first_exploration = [
        item["enquiry_id"] for item in first["selected"]
        if item["allocation_type"] == "EXPLORATION"
    ]
    second_exploration = [
        item["enquiry_id"] for item in second["selected"]
        if item["allocation_type"] == "EXPLORATION"
    ]
    assert first_exploration == second_exploration


def test_ranking_is_independent_within_each_cohort_and_ordered_for_allocation():
    source = [
        {"enquiry_id": "B-2", "cohort": "B", "product_family": "Motor_DongCo"},
        {"enquiry_id": "A-1", "cohort": "A", "product_family": "Motor_DongCo"},
        {"enquiry_id": "B-1", "cohort": "B", "product_family": "Motor_DongCo"},
    ]

    ranked = rank_enquiries_by_cohort(source, now=NOW)

    assert [(item["enquiry_id"], item["cohort_rank"]) for item in ranked] == [
        ("A-1", 1),
        ("B-1", 1),
        ("B-2", 2),
    ]
    assert all(item["score_mode"] == "RULES_BASELINE" for item in ranked)
    assert all(item["score_unit"] == "points" for item in ranked)


def test_daily_snapshot_is_idempotent_and_regeneration_is_audited(tmp_path):
    db_path = tmp_path / "allocation.db"
    items = _ranked_items(20)
    first = get_or_create_daily_allocation(
        db_path,
        items,
        business_date="2026-11-14",
        capacity_k=10,
        seed=11,
    )
    repeated = get_or_create_daily_allocation(
        db_path,
        _ranked_items(5),
        business_date="2026-11-14",
        capacity_k=5,
        seed=99,
    )

    assert repeated["revision"] == first["revision"] == 1
    assert repeated["exploration_seed"] == 11
    assert repeated["selected"] == first["selected"]

    regenerated = regenerate_daily_allocation(
        db_path,
        items,
        business_date="2026-11-14",
        capacity_k=10,
        actor_id="supervisor-1",
        reason="Approved source refresh",
        seed=12,
    )
    with sqlite3.connect(db_path) as conn:
        audit = conn.execute(
            """
            SELECT revision, action, actor_id, reason, seed
            FROM daily_queue_allocation_audit
            ORDER BY id
            """
        ).fetchall()

    assert regenerated["revision"] == 2
    assert audit == [
        (1, "CREATED", "SYSTEM", None, 11),
        (2, "REGENERATED", "supervisor-1", "Approved source refresh", 12),
    ]
