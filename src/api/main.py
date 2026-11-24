
"""PLPT-16, PLPT-17 & PLPT-41: Queue API and overdue escalation."""

import csv
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.queue_allocation import (
    MAX_SEED,
    configured_capacity_k,
    get_daily_allocation,
    get_or_create_daily_allocation,
    rank_enquiries_by_cohort,
)
from src.rules.rules_engine import RULES_LABEL, _parse_datetime, build_queue
from src.rules.vietnamese_business_calendar import (
    is_overdue_after_business_days,
)


VN_TZ = timezone(timedelta(hours=7))
STALE_HOLD_HOURS = 24.0

QUARANTINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "quarantine"
    / "quarantine_rows.csv"
)


# ==========================================================
# PLPT-41: Vietnam datetime and overdue helpers
# ==========================================================

def _to_vn_datetime(value: Any) -> datetime | None:
    """Parse timestamp and normalize it to Vietnam timezone."""
    if value is None or str(value).strip() == "":
        return None

    parsed = _parse_datetime(value)

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=VN_TZ)

    return parsed.astimezone(VN_TZ)


def _get_overdue_status(
    enquiry: dict[str, Any],
    now: datetime,
) -> bool:
    """Determine whether an enquiry missed its first-review SLA."""

    if enquiry.get("first_reviewed_at"):
        return False

    eligible_at = (
        enquiry.get("queue_eligible_at")
        or enquiry.get("eligible_at")
        or enquiry.get("t0_time")
    )

    eligible_datetime = _to_vn_datetime(eligible_at)

    if eligible_datetime is None:
        return False

    return is_overdue_after_business_days(
        eligible_datetime,
        now,
        business_days=2,
    )


app = FastAPI(
    title="SongNhat AI-02 Predictive Lead Prioritization API",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_db_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]

    return Path(
        os.getenv(
            "DATABASE_PATH",
            str(project_root / "data" / "songnhat.db"),
        )
    )


def _ensure_blocked_table(conn: sqlite3.Connection) -> None:
    """Tạo bảng blocked_accounts nếu chưa tồn tại (PLPT-17)."""

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
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(blocked_accounts)"
        ).fetchall()
    }

    if "reason" not in existing_cols:
        conn.execute(
            "ALTER TABLE blocked_accounts ADD COLUMN reason TEXT"
        )

    if "created_by" not in existing_cols:
        conn.execute(
            """
            ALTER TABLE blocked_accounts
            ADD COLUMN created_by TEXT DEFAULT 'PO_OPERATOR'
            """
        )

    conn.commit()


def load_enquiries_and_meta() -> tuple[list[dict[str, Any]], set[str], set[str], float | None]:
    """
    Đọc clean_enquiries hoặc fallback raw_enquiries,
    loại bỏ hồ sơ đã đóng hoặc đã rút và lấy danh sách blocked.
    """

    db_path = _get_db_path()

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}"
        )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        has_clean = cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='clean_enquiries'
            """
        ).fetchone()

        has_raw = cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='raw_enquiries'
            """
        ).fetchone()

        table_name = (
            "clean_enquiries"
            if has_clean
            else ("raw_enquiries" if has_raw else None)
        )

        if table_name is None:
            return [], set(), set(), None

        cursor.execute(
            f"SELECT * FROM {table_name} "
            f"WHERE COALESCE(LOWER(TRIM(workflow_state)), '') NOT IN ('closed', 'withdrawn')"
        )

        enquiries = [
            dict(row)
            for row in cursor.fetchall()
        ]

        blocked_enquiries: set[str] = set()
        blocked_accounts: set[str] = set()

        has_blocked_table = cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='blocked_accounts'
            """
        ).fetchone()

        if has_blocked_table:
            for row in cursor.execute(
                "SELECT * FROM blocked_accounts"
            ).fetchall():

                row_dict = dict(row)

                enq_val = str(
                    row_dict.get("enquiry_id") or ""
                ).strip()

                acct_val = str(
                    row_dict.get("account_key") or ""
                ).strip()

                if enq_val:
                    blocked_enquiries.add(enq_val)
                elif acct_val:
                    blocked_accounts.add(acct_val)

        # Tính source_age_hours dựa trên extraction_time; nếu rỗng hoặc bất kỳ hồ sơ còn hiệu lực nào thiếu/sai định dạng -> None
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
        )


def _read_quarantine_records() -> tuple[
    list[dict[str, Any]],
    bool,
]:
    if not QUARANTINE_PATH.exists():
        return [], False

    permitted_fields = (
        "enquiry_id",
        "account_key",
        "cohort",
        "source",
        "t0_time",
        "extraction_time",
        "product_family",
        "quarantine_reason",
    )

    with QUARANTINE_PATH.open(
        encoding="utf-8-sig",
        newline="",
    ) as quarantine_file:

        rows = csv.DictReader(quarantine_file)

        records = [
            {
                field: row.get(field)
                for field in permitted_fields
                if field in row
            }
            for row in rows
        ]

    return records, True


def _snapshot_age_seconds(
    allocation: dict[str, Any] | None,
) -> float | None:

    if allocation is None:
        return None

    created_at = _parse_datetime(
        allocation.get("created_at")
    )

    if created_at is None:
        raise ValueError(
            "Daily allocation has an invalid created_at timestamp"
        )

    now_utc = datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(
            tzinfo=timezone.utc
        )

    return round(
        max(
            0.0,
            (now_utc - created_at).total_seconds(),
        ),
        2,
    )


def _snapshot_model_ranks(
    allocation: dict[str, Any] | None,
) -> dict[str, int]:

    if allocation is None:
        return {}

    candidates = (
        allocation["selected"]
        + allocation["deferred"]
    )

    cohorts: dict[str, list[dict[str, Any]]] = {}

    for item in candidates:
        cohort_id = str(
            item.get("cohort_id")
            or item.get("cohort")
            or ""
        )

        cohorts.setdefault(
            cohort_id,
            [],
        ).append(item)

    ranks: dict[str, int] = {}

    for cohort_items in cohorts.values():

        def model_order_key(
            item: dict[str, Any],
        ) -> tuple[float, float, str]:

            parsed_t0 = _parse_datetime(
                item.get("t0_time")
            )

            return (
                -float(item.get("rules_score") or 0),
                (
                    parsed_t0.timestamp()
                    if parsed_t0 is not None
                    else float("inf")
                ),
                str(item.get("enquiry_id") or ""),
            )

        ordered = sorted(
            cohort_items,
            key=model_order_key,
        )

        for index, item in enumerate(
            ordered,
            start=1,
        ):
            stored_rank = item.get("model_rank")

            ranks[
                str(item.get("enquiry_id") or "")
            ] = (
                stored_rank
                if isinstance(stored_rank, int)
                else index
            )

    return ranks


def _workspace_queue_item(
    item: dict[str, Any],
    *,
    final_work_order: int | None,
    snapshot_age: float | None,
) -> dict[str, Any]:

    timestamp_field = item.get("timestamp_source")

    source_timestamp = (
        item.get(timestamp_field)
        if timestamp_field in {
            "t0_time",
            "feature_event_time",
        }
        else None
    )

    return {
        "enquiry_id": str(
            item.get("enquiry_id") or ""
        ),
        "account_key": item.get("account_key"),
        "cohort_id": (
            item.get("cohort_id")
            or item.get("cohort")
        ),
        "owner": item.get("owner"),
        "model_rank": item.get("model_rank"),
        "final_work_order": final_work_order,
        "snapshot_age": snapshot_age,

        # PLPT-41
        "is_overdue_2bd": bool(
            item.get("is_overdue_2bd", False)
        ),

        "score_mode": item.get("score_mode"),
        "score": item.get("rules_score"),
        "score_unit": item.get("score_unit"),
        "allocation_type": item.get("allocation_type"),
        "contract_due_date": item.get("contract_due_date"),

        "contributing_factors": [
            {
                "factor": factor.get("factor"),
                "value": factor.get("points"),
                "reason": factor.get("reason"),
                "source_timestamp": source_timestamp,
                "source_timestamp_field": timestamp_field,
            }
            for factor in (
                item.get("top_contributors") or []
            )[:3]
        ],
    }


def _unscored_workspace_item(
    enquiry: dict[str, Any],
    snapshot_age: float | None,
) -> dict[str, Any]:

    return {
        "enquiry_id": str(
            enquiry.get("enquiry_id") or ""
        ),
        "account_key": enquiry.get("account_key"),
        "cohort_id": (
            enquiry.get("cohort_id")
            or enquiry.get("cohort")
        ),
        "owner": enquiry.get("owner"),
        "model_rank": None,
        "final_work_order": None,
        "snapshot_age": snapshot_age,
        "score_mode": None,
        "score": None,
        "score_unit": None,
        "allocation_type": None,
        "contract_due_date": enquiry.get(
            "contract_due_date"
        ),
        "is_overdue_2bd": bool(
            enquiry.get("is_overdue_2bd", False)
        ),
        "contributing_factors": [],
        "unscored_reason": (
            "No valid scoring result in the daily allocation snapshot"
        ),
    }


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/block")
def block_lead_or_account(
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:

    enquiry_id = str(
        payload.get("enquiry_id")
        or payload.get("enquiry")
        or payload.get("lead_id")
        or payload.get("id")
        or ""
    ).strip()

    account_key = str(
        payload.get("account_key")
        or payload.get("account")
        or payload.get("customer_id")
        or ""
    ).strip()
    block_scope = str(payload.get("block_scope") or "").strip().lower()
    block_account = bool(payload.get("block_account")) or block_scope == "account"
    reason = str(
        payload.get("reason")
        or payload.get("block_reason")
        or "Emergency block by operator"
    ).strip()

    created_by = str(
        payload.get("created_by")
        or "PO_OPERATOR"
    ).strip()

    if not enquiry_id and not account_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cần cung cấp ít nhất một trong hai trường: "
                "enquiry_id hoặc account_key."
            ),
        )

    # Nếu gửi enquiry_id mà không chọn rõ chế độ chặn toàn tài khoản, chỉ chặn theo enquiry_id
    if enquiry_id and not block_account:
        target_enq: str | None = enquiry_id
        target_acct: str | None = None
    else:
        if not account_key:
            raise HTTPException(
                status_code=400,
                detail="Cần cung cấp account_key khi chọn chế độ chặn toàn tài khoản.",
            )
        target_enq = None
        target_acct = account_key

    db_path = _get_db_path()

    if not db_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not found: {db_path}",
        )

    now_iso = datetime.now(
        VN_TZ
    ).replace(
        microsecond=0
    ).isoformat()

    try:
        with sqlite3.connect(str(db_path)) as conn:

            _ensure_blocked_table(conn)
            if target_enq is not None:
                existing = conn.execute(
                    """
                    SELECT enquiry_id, account_key, reason, blocked_at
                    FROM blocked_accounts
                    WHERE enquiry_id = ?
                    LIMIT 1
                    """,
                    (target_enq,),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT enquiry_id, account_key, reason, blocked_at
                    FROM blocked_accounts
                    WHERE COALESCE(enquiry_id, '') = '' AND account_key = ?
                    LIMIT 1
                    """,
                    (target_acct,),
                ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO blocked_accounts
                    (enquiry_id, account_key, reason, blocked_at, created_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (target_enq, target_acct, reason, now_iso, created_by),
                )

                conn.commit()
                blocked_at = now_iso
                enquiry_id = target_enq or ""
                account_key = target_acct or ""
            else:
                enquiry_id = existing[0] or ""
                account_key = existing[1] or ""
                reason = existing[2] or reason
                blocked_at = existing[3] or now_iso

        return {
            "blocked": True,
            "status": "blocked",
            "enquiry_id": enquiry_id,
            "account_key": account_key,
            "reason": reason,
            "blocked_at": blocked_at,
        }

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}",
        )


def _count_quarantine_rows() -> int:
    project_root = Path(__file__).resolve().parents[2]
    quarantine_dir = Path(os.getenv("QUARANTINE_DIR", str(project_root / "data" / "quarantine")))
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


def _source_freshness_warning(source_age_hours: float | None) -> str | None:
    if source_age_hours is None:
        return (
            "Không xác minh được độ tươi dữ liệu nguồn "
            "(tập dữ liệu rỗng hoặc thiếu/sai định dạng extraction_time). "
            "Hàng đợi tạm khóa để đảm bảo an toàn vận hành."
        )
    if source_age_hours > STALE_HOLD_HOURS:
        return (
            f"Dữ liệu nguồn đã cũ {source_age_hours} giờ (vượt ngưỡng 24 giờ). "
            "Hàng đợi tạm khóa để đảm bảo an toàn vận hành."
        )
    return None


@app.get("/api/queue")
def get_queue(
    limit: int = Query(default=25, ge=1, le=1000),
) -> dict[str, Any]:

    try:
        (
            enquiries,
            blocked_enquiries,
            blocked_accounts,
            source_age_hours,
        ) = load_enquiries_and_meta()

        now_vn = datetime.now(VN_TZ)

        queue = build_queue(
            enquiries,
            now=now_vn,
            exclude_closed=True,
        )

        active_queue = []

        for entry in queue:

            enq_id = str(
                entry.get("enquiry_id") or ""
            ).strip()

            acct_key = str(
                entry.get("account_key") or ""
            ).strip()

            if (
                enq_id and enq_id in blocked_enquiries
            ) or (
                acct_key and acct_key in blocked_accounts
            ):
                continue

            active_queue.append(entry)

        warning = _source_freshness_warning(source_age_hours)
        is_stale = warning is not None
        status = "HOLD_STALE_DATA" if is_stale else "OK"

        items: list[dict[str, Any]] = []

        if not is_stale:

            for idx, entry in enumerate(
                active_queue[:limit],
                start=1,
            ):
                items.append(
                    {
                        "enquiry_id": str(
                            entry.get("enquiry_id") or ""
                        ),
                        "account_key": str(
                            entry.get("account_key") or ""
                        ),
                        "final_rank": idx,
                        "rule_score": float(
                            entry["rules_score"]
                        ),
                        "score_mode": RULES_LABEL,
                        "is_urgent": bool(
                            entry["tier0_bypass"]
                        ),
                        "is_blocked": False,
                        "reasons": list(
                            entry.get("reasons") or []
                        ),
                        "t0_time": str(
                            entry.get("t0_time") or ""
                        ),
                    }
                )

        return {
            "status": status,
            "locked": is_stale,
            "warning": warning,
            "source_age_hours": source_age_hours,
            "quarantine_count": _count_quarantine_rows(),
            "total": len(active_queue) if not is_stale else 0,
            "items": items,
            "data": items,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}",
        )


@app.post("/api/v1/workspace/queue/daily")
def create_daily_workspace_queue(
    seed: int | None = Query(
        default=None,
        ge=0,
        le=MAX_SEED,
    ),
) -> dict[str, Any]:

    try:
        db_path = _get_db_path()

        if not db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}"
            )

        with sqlite3.connect(str(db_path)) as conn:

            has_source = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('clean_enquiries', 'raw_enquiries')
                LIMIT 1
                """
            ).fetchone()

        if has_source is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No clean_enquiries or raw_enquiries "
                    "source table is available"
                ),
            )

        (
            enquiries,
            blocked_enquiries,
            blocked_accounts,
            source_age_hours,
        ) = load_enquiries_and_meta()

        warning = _source_freshness_warning(source_age_hours)

        if warning is not None:
            return {
                "status": "HELD_STALE_DATA",
                "locked": True,
                "source_age_hours": source_age_hours,
                "warning": warning,
                "allocation": None,
            }

        active_enquiries = []

        for enquiry in enquiries:

            enquiry_id = str(
                enquiry.get("enquiry_id") or ""
            ).strip()

            account_key = str(
                enquiry.get("account_key") or ""
            ).strip()

            if (
                enquiry_id and enquiry_id in blocked_enquiries
            ) or (
                account_key and account_key in blocked_accounts
            ):
                continue

            active_enquiries.append(enquiry)

        now_vn = datetime.now(VN_TZ)

        ranked = rank_enquiries_by_cohort(
            active_enquiries,
            now=now_vn,
        )

        allocation = get_or_create_daily_allocation(
            db_path,
            ranked,
            business_date=now_vn.date(),
            capacity_k=configured_capacity_k(),
            seed=seed,
        )

        return {
            "status": allocation["status"],
            "locked": False,
            "source_age_hours": source_age_hours,
            "allocation": allocation,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail=str(error),
        )

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}",
        )


@app.get("/api/v1/workspace/queue")
def get_workspace_queue(
    view: Literal[
        "Active",
        "Unscored",
        "Quarantine",
    ] = Query(default="Active"),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
    ),
) -> dict[str, Any]:

    """Read one workspace view without changing a queue snapshot."""

    try:
        db_path = _get_db_path()

        if not db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}"
            )

        with sqlite3.connect(str(db_path)) as conn:

            has_source = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('clean_enquiries', 'raw_enquiries')
                LIMIT 1
                """
            ).fetchone()

        if has_source is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No clean_enquiries or raw_enquiries "
                    "source table is available"
                ),
            )

        (
            enquiries,
            blocked_enquiries,
            blocked_accounts,
            source_age_hours,
        ) = load_enquiries_and_meta()

        active_source = [
            enquiry
            for enquiry in enquiries
            if (
                str(enquiry.get("enquiry_id") or "").strip()
                not in blocked_enquiries
            )
            and (
                str(enquiry.get("account_key") or "").strip()
                not in blocked_accounts
            )
        ]

        active_source_ids = {
            str(
                enquiry.get("enquiry_id") or ""
            ).strip()
            for enquiry in active_source
        }

        now_vn = datetime.now(VN_TZ)

        allocation = get_daily_allocation(
            db_path,
            now_vn.date(),
        )

        snapshot_age = _snapshot_age_seconds(
            allocation
        )

        model_ranks = _snapshot_model_ranks(
            allocation
        )

        warning = _source_freshness_warning(source_age_hours)

        classification_basis = None

        # ==================================================
        # PLPT-41: Two-Business-Day Overdue Escalation
        # ==================================================

        source_by_id = {
            str(
                enquiry.get("enquiry_id") or ""
            ).strip(): enquiry
            for enquiry in active_source
        }

        overdue_by_id: dict[str, bool] = {}

        for enquiry in active_source:

            enquiry_id = str(
                enquiry.get("enquiry_id") or ""
            ).strip()

            overdue_by_id[enquiry_id] = (
                _get_overdue_status(
                    enquiry,
                    now_vn,
                )
            )

        overdue_2bd_count = sum(
            overdue_by_id.values()
        )

        capacity_k = (
            allocation.get("capacity_k")
            if allocation
            else configured_capacity_k()
        )

        # Tier 0 mandatory workload
        mandatory_count = sum(
            1
            for enquiry in active_source
            if (
                str(
                    enquiry.get(
                        "is_urgent_replacement",
                        "",
                    )
                ).lower() == "true"
                or str(
                    enquiry.get(
                        "has_contractual_sla",
                        "",
                    )
                ).lower() == "true"
            )
        )

        is_overloaded = (
            mandatory_count >= capacity_k
        )

        # ==================================================
        # Active View
        # ==================================================

        if view == "Active":

            selected = (
                allocation["selected"]
                if allocation is not None
                else []
            )

            selected = [
                item
                for item in selected
                if str(
                    item.get("enquiry_id") or ""
                ).strip() in active_source_ids
            ]

            items = []

            for index, item in enumerate(
                selected,
                start=1,
            ):

                enquiry_id = str(
                    item.get("enquiry_id") or ""
                ).strip()

                source = source_by_id.get(
                    enquiry_id,
                    {},
                )

                combined = {
                    **source,
                    **item,

                    "first_reviewed_at": (
                        item.get("first_reviewed_at")
                        or source.get("first_reviewed_at")
                    ),

                    "queue_eligible_at": (
                        item.get("queue_eligible_at")
                        or source.get("queue_eligible_at")
                    ),

                    "eligible_at": (
                        item.get("eligible_at")
                        or source.get("eligible_at")
                    ),

                    "t0_time": (
                        item.get("t0_time")
                        or source.get("t0_time")
                    ),

                    "is_overdue_2bd": (
                        overdue_by_id.get(
                            enquiry_id,
                            False,
                        )
                    ),

                    "model_rank": model_ranks.get(
                        enquiry_id
                    ),
                }

                workspace_item = _workspace_queue_item(
                    combined,
                    final_work_order=index,
                    snapshot_age=snapshot_age,
                )

                items.append(workspace_item)

            if warning is not None:
                status = "HELD_STALE_DATA"
                items = []

            elif allocation is None:
                status = "NO_DAILY_SNAPSHOT"

            else:
                status = allocation["status"]

            total = len(items)
            source_available = True

        # ==================================================
        # Unscored View
        # ==================================================

        elif view == "Unscored":

            classification_basis = (
                "Not present in the persisted daily scoring snapshot"
            )

            snapshot_ids = set()

            if allocation is not None:
                snapshot_ids = {
                    str(
                        item.get("enquiry_id") or ""
                    ).strip()
                    for item in (
                        allocation["selected"]
                        + allocation["deferred"]
                    )
                }

            unscored = [
                enquiry
                for enquiry in active_source
                if str(
                    enquiry.get("enquiry_id") or ""
                ).strip() not in snapshot_ids
            ]

            items = []

            for enquiry in unscored:

                enquiry_id = str(
                    enquiry.get("enquiry_id") or ""
                ).strip()

                enriched_enquiry = {
                    **enquiry,
                    "is_overdue_2bd": (
                        overdue_by_id.get(
                            enquiry_id,
                            False,
                        )
                    ),
                }

                items.append(
                    _unscored_workspace_item(
                        enriched_enquiry,
                        snapshot_age,
                    )
                )

            total = len(items)

            status = (
                "OK"
                if allocation is not None
                else "NO_DAILY_SNAPSHOT"
            )

            source_available = True

        # ==================================================
        # Quarantine View
        # ==================================================

        else:

            quarantine_records, source_available = (
                _read_quarantine_records()
            )

            items = [
                {
                    "enquiry_id": str(
                        record.get("enquiry_id") or ""
                    ),
                    "account_key": record.get("account_key"),
                    "cohort_id": record.get("cohort"),
                    "owner": record.get("owner"),
                    "model_rank": None,
                    "final_work_order": None,
                    "snapshot_age": snapshot_age,
                    "score_mode": None,
                    "score": None,
                    "score_unit": None,
                    "allocation_type": None,
                    "contract_due_date": None,
                    "is_overdue_2bd": False,
                    "contributing_factors": [],
                    "quarantine_reason": record.get(
                        "quarantine_reason"
                    ),
                }
                for record in quarantine_records
            ]

            total = len(items)

            status = (
                "OK"
                if source_available
                else "QUARANTINE_SOURCE_UNAVAILABLE"
            )

        # ==================================================
        # Workspace API Response
        # ==================================================

        return {
            "view": view,
            "status": status,

            "snapshot_age": snapshot_age,
            "snapshot_age_unit": "seconds",
            "locked": status == "HELD_STALE_DATA",
            "warning": warning if status == "HELD_STALE_DATA" else None,

            "snapshot_created_at": (
                allocation.get("created_at")
                if allocation
                else None
            ),

            "source_age_hours": source_age_hours,

            "capacity_k": capacity_k,

            # PLPT-41
            "overdue_2bd_count": overdue_2bd_count,
            "mandatory_count": mandatory_count,
            "is_overloaded": is_overloaded,

            "scheduled_today": (
                len(allocation["selected"])
                if allocation
                else 0
            ),

            "exploration_slots": (
                allocation.get("exploration_slots", 0)
                if allocation
                else 0
            ),

            "view_source_available": source_available,
            "classification_basis": classification_basis,

            "total": total,
            "items": items[:limit],
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )

    except ValueError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

    except sqlite3.Error as error:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {error}",
        )

from src.api.monitoring import router as monitoring_router
app.include_router(monitoring_router)

from src.api.monitoring import router as monitoring_router
app.include_router(monitoring_router)
