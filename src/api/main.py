"""PLPT-16 & PLPT-17: FastAPI Queue Endpoint + Emergency Block & Stale Data Hold Controls."""

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

VN_TZ = timezone(timedelta(hours=7))
STALE_HOLD_HOURS = 24.0
QUARANTINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "quarantine"
    / "quarantine_rows.csv"
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
        row[1] for row in conn.execute("PRAGMA table_info(blocked_accounts)").fetchall()
    }
    if "reason" not in existing_cols:
        conn.execute("ALTER TABLE blocked_accounts ADD COLUMN reason TEXT")
    if "created_by" not in existing_cols:
        conn.execute(
            "ALTER TABLE blocked_accounts ADD COLUMN created_by TEXT DEFAULT 'PO_OPERATOR'"
        )
    conn.commit()


def load_enquiries_and_meta() -> tuple[list[dict[str, Any]], set[str], set[str], float]:
    """
    Đọc bảng clean_enquiries (hoặc fallback sang raw_enquiries),
    loại bỏ các bản ghi đã đóng (workflow_state = 'Closed'),
    lấy danh sách chặn (nếu bảng blocked_accounts tồn tại) và tính tuổi dữ liệu.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
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
            return [], set(), set(), 0.0

        cursor.execute(
            f"SELECT * FROM {table_name} "
            f"WHERE COALESCE(LOWER(TRIM(workflow_state)), '') != 'closed'"
        )
        enquiries = [dict(row) for row in cursor.fetchall()]

        # Kiểm tra bảng blocked_accounts (US-09 / PLPT-17)
        blocked_enquiries: set[str] = set()
        blocked_accounts: set[str] = set()
        has_blocked_table = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='blocked_accounts'"
        ).fetchone()
        if has_blocked_table:
            for row in cursor.execute("SELECT * FROM blocked_accounts").fetchall():
                row_dict = dict(row)
                enq_val = str(row_dict.get("enquiry_id") or "").strip()
                acct_val = str(row_dict.get("account_key") or "").strip()
                if enq_val:
                    blocked_enquiries.add(enq_val)
                if acct_val:
                    blocked_accounts.add(acct_val)

        # Tính source_age_hours dựa trên extraction_time mới nhất trong bảng
        latest_ext = None
        for row in enquiries:
            ext_dt = _parse_datetime(row.get("extraction_time"))
            if ext_dt is not None:
                if ext_dt.tzinfo is None:
                    ext_dt = ext_dt.replace(tzinfo=VN_TZ)
                if latest_ext is None or ext_dt > latest_ext:
                    latest_ext = ext_dt

        if latest_ext is not None:
            now_vn = datetime.now(VN_TZ)
            age_hours = max(0.0, round((now_vn - latest_ext).total_seconds() / 3600.0, 2))
        else:
            age_hours = 0.0

        return enquiries, blocked_enquiries, blocked_accounts, age_hours


def _read_quarantine_records() -> tuple[list[dict[str, Any]], bool]:
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
    with QUARANTINE_PATH.open(encoding="utf-8-sig", newline="") as quarantine_file:
        rows = csv.DictReader(quarantine_file)
        records = [
            {field: row.get(field) for field in permitted_fields if field in row}
            for row in rows
        ]
    return records, True


def _snapshot_age_seconds(allocation: dict[str, Any] | None) -> float | None:
    if allocation is None:
        return None
    created_at = _parse_datetime(allocation.get("created_at"))
    if created_at is None:
        raise ValueError("Daily allocation has an invalid created_at timestamp")
    now_utc = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return round(max(0.0, (now_utc - created_at).total_seconds()), 2)


def _snapshot_model_ranks(
    allocation: dict[str, Any] | None,
) -> dict[str, int]:
    if allocation is None:
        return {}

    candidates = allocation["selected"] + allocation["deferred"]
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        cohort_id = str(item.get("cohort_id") or item.get("cohort") or "")
        cohorts.setdefault(cohort_id, []).append(item)

    ranks: dict[str, int] = {}
    for cohort_items in cohorts.values():
        def model_order_key(item: dict[str, Any]) -> tuple[float, float, str]:
            parsed_t0 = _parse_datetime(item.get("t0_time"))
            return (
                -float(item.get("rules_score") or 0),
                parsed_t0.timestamp() if parsed_t0 is not None else float("inf"),
                str(item.get("enquiry_id") or ""),
            )

        ordered = sorted(
            cohort_items,
            key=model_order_key,
        )
        for index, item in enumerate(ordered, start=1):
            stored_rank = item.get("model_rank")
            ranks[str(item.get("enquiry_id") or "")] = (
                stored_rank if isinstance(stored_rank, int) else index
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
        if timestamp_field in {"t0_time", "feature_event_time"}
        else None
    )
    return {
        "enquiry_id": str(item.get("enquiry_id") or ""),
        "account_key": item.get("account_key"),
        "cohort_id": item.get("cohort_id") or item.get("cohort"),
        "owner": item.get("owner"),
        "model_rank": item.get("model_rank"),
        "final_work_order": final_work_order,
        "snapshot_age": snapshot_age,
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
            for factor in (item.get("top_contributors") or [])[:3]
        ],
    }


def _unscored_workspace_item(
    enquiry: dict[str, Any],
    snapshot_age: float | None,
) -> dict[str, Any]:
    return {
        "enquiry_id": str(enquiry.get("enquiry_id") or ""),
        "account_key": enquiry.get("account_key"),
        "cohort_id": enquiry.get("cohort_id") or enquiry.get("cohort"),
        "owner": enquiry.get("owner"),
        "model_rank": None,
        "final_work_order": None,
        "snapshot_age": snapshot_age,
        "score_mode": None,
        "score": None,
        "score_unit": None,
        "allocation_type": None,
        "contract_due_date": enquiry.get("contract_due_date"),
        "contributing_factors": [],
        "unscored_reason": "No valid scoring result in the daily allocation snapshot",
    }


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/block")
def block_lead_or_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """
    PLPT-17: Kích hoạt chặn khẩn cấp theo enquiry_id và/hoặc account_key.
    Hồ sơ bị chặn sẽ biến mất ngay lập tức khỏi GET /api/queue.
    """
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
    reason = str(
        payload.get("reason")
        or payload.get("block_reason")
        or "Emergency block by operator"
    ).strip()
    created_by = str(payload.get("created_by") or "PO_OPERATOR").strip()

    if not enquiry_id and not account_key:
        raise HTTPException(
            status_code=400,
            detail="Cần cung cấp ít nhất một trong hai trường: enquiry_id hoặc account_key.",
        )

    db_path = _get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=503, detail=f"Database not found: {db_path}")

    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    try:
        with sqlite3.connect(str(db_path)) as conn:
            _ensure_blocked_table(conn)
            existing = conn.execute(
                """
                SELECT enquiry_id, account_key, reason, blocked_at
                FROM blocked_accounts
                WHERE (COALESCE(enquiry_id, '') != '' AND enquiry_id = ?)
                   OR (COALESCE(account_key, '') != '' AND account_key = ?)
                LIMIT 1
                """,
                (enquiry_id, account_key),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO blocked_accounts (enquiry_id, account_key, reason, blocked_at, created_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (enquiry_id or None, account_key or None, reason, now_iso, created_by),
                )
                conn.commit()
                blocked_at = now_iso
            else:
                enquiry_id = existing[0] or enquiry_id
                account_key = existing[1] or account_key
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
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.get("/api/queue")
def get_queue(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """
    Trả về danh sách xếp hạng theo đúng định dạng khóa cứng mục 6 (PLPT-16)
    và tích hợp kiểm soát chặn khẩn cấp + khóa dữ liệu quá hạn 24h (PLPT-17).
    """
    try:
        enquiries, blocked_enquiries, blocked_accounts, source_age_hours = (
            load_enquiries_and_meta()
        )
        now_vn = datetime.now(VN_TZ)
        queue = build_queue(enquiries, now=now_vn, exclude_closed=True)

        # Lọc bỏ hồ sơ bị chặn trước khi đánh số lại final_rank
        active_queue = []
        for entry in queue:
            enq_id = str(entry.get("enquiry_id") or "").strip()
            acct_key = str(entry.get("account_key") or "").strip()
            if (enq_id and enq_id in blocked_enquiries) or (
                acct_key and acct_key in blocked_accounts
            ):
                continue
            active_queue.append(entry)

        is_stale = source_age_hours > STALE_HOLD_HOURS
        status = "HELD_STALE_DATA" if is_stale else "OK"
        warning = (
            f"Dữ liệu nguồn đã cũ {source_age_hours} giờ (vượt ngưỡng 24 giờ). "
            "Hàng đợi tạm khóa (HELD_STALE_DATA) để đảm bảo an toàn vận hành."
            if is_stale
            else None
        )

        items: list[dict[str, Any]] = []
        if not is_stale:
            for idx, entry in enumerate(active_queue[:limit], start=1):
                items.append(
                    {
                        "enquiry_id": str(entry.get("enquiry_id") or ""),
                        "account_key": str(entry.get("account_key") or ""),
                        "final_rank": idx,
                        "rule_score": float(entry["rules_score"]),
                        "score_mode": RULES_LABEL,
                        "is_urgent": bool(entry["tier0_bypass"]),
                        "is_blocked": False,
                        "reasons": list(entry.get("reasons") or []),
                        "t0_time": str(entry.get("t0_time") or ""),
                    }
                )

        return {
            "status": status,
            "locked": is_stale,
            "warning": warning,
            "source_age_hours": source_age_hours,
            "total": len(active_queue) if not is_stale else 0,
            "items": items,
            "data": items,
        }

    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.post("/api/v1/workspace/queue/daily")
def create_daily_workspace_queue(
    seed: int | None = Query(default=None, ge=0, le=MAX_SEED),
) -> dict[str, Any]:
    """Create or return the persisted, idempotent daily review allocation."""
    try:
        db_path = _get_db_path()
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
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
                detail="No clean_enquiries or raw_enquiries source table is available",
            )

        enquiries, blocked_enquiries, blocked_accounts, source_age_hours = (
            load_enquiries_and_meta()
        )
        is_stale = source_age_hours > STALE_HOLD_HOURS
        if is_stale:
            return {
                "status": "HELD_STALE_DATA",
                "locked": True,
                "source_age_hours": source_age_hours,
                "allocation": None,
            }

        active_enquiries = []
        for enquiry in enquiries:
            enquiry_id = str(enquiry.get("enquiry_id") or "").strip()
            account_key = str(enquiry.get("account_key") or "").strip()
            if (enquiry_id and enquiry_id in blocked_enquiries) or (
                account_key and account_key in blocked_accounts
            ):
                continue
            active_enquiries.append(enquiry)

        now_vn = datetime.now(VN_TZ)
        ranked = rank_enquiries_by_cohort(active_enquiries, now=now_vn)
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
        raise HTTPException(status_code=503, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")


@app.get("/api/v1/workspace/queue")
def get_workspace_queue(
    view: Literal["Active", "Unscored", "Quarantine"] = Query(default="Active"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Read one workspace view without creating or changing a queue snapshot."""
    try:
        db_path = _get_db_path()
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")
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
                detail="No clean_enquiries or raw_enquiries source table is available",
            )

        enquiries, blocked_enquiries, blocked_accounts, source_age_hours = (
            load_enquiries_and_meta()
        )
        active_source = [
            enquiry
            for enquiry in enquiries
            if str(enquiry.get("enquiry_id") or "").strip() not in blocked_enquiries
            and str(enquiry.get("account_key") or "").strip() not in blocked_accounts
        ]
        active_source_ids = {
            str(enquiry.get("enquiry_id") or "").strip()
            for enquiry in active_source
        }

        now_vn = datetime.now(VN_TZ)
        allocation = get_daily_allocation(db_path, now_vn.date())
        snapshot_age = _snapshot_age_seconds(allocation)
        model_ranks = _snapshot_model_ranks(allocation)
        is_stale = source_age_hours > STALE_HOLD_HOURS
        classification_basis = None

        if view == "Active":
            selected = allocation["selected"] if allocation is not None else []
            selected = [
                item
                for item in selected
                if str(item.get("enquiry_id") or "").strip() in active_source_ids
            ]
            items = [
                _workspace_queue_item(
                    {
                        **item,
                        "model_rank": model_ranks.get(
                            str(item.get("enquiry_id") or "")
                        ),
                    },
                    final_work_order=index,
                    snapshot_age=snapshot_age,
                )
                for index, item in enumerate(selected, start=1)
            ]
            if is_stale:
                status = "HELD_STALE_DATA"
                items = []
            elif allocation is None:
                status = "NO_DAILY_SNAPSHOT"
            else:
                status = allocation["status"]
            total = len(items)
            source_available = True
        elif view == "Unscored":
            classification_basis = "Not present in the persisted daily scoring snapshot"
            snapshot_ids = set()
            if allocation is not None:
                snapshot_ids = {
                    str(item.get("enquiry_id") or "").strip()
                    for item in allocation["selected"] + allocation["deferred"]
                }
            unscored = [
                enquiry
                for enquiry in active_source
                if str(enquiry.get("enquiry_id") or "").strip() not in snapshot_ids
            ]
            items = [
                _unscored_workspace_item(enquiry, snapshot_age)
                for enquiry in unscored
            ]
            total = len(items)
            status = "OK" if allocation is not None else "NO_DAILY_SNAPSHOT"
            source_available = True
        else:
            quarantine_records, source_available = _read_quarantine_records()
            items = [
                {
                    "enquiry_id": str(record.get("enquiry_id") or ""),
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
                    "contributing_factors": [],
                    "quarantine_reason": record.get("quarantine_reason"),
                }
                for record in quarantine_records
            ]
            total = len(items)
            status = "OK" if source_available else "QUARANTINE_SOURCE_UNAVAILABLE"

        return {
            "view": view,
            "status": status,
            "snapshot_age": snapshot_age,
            "snapshot_age_unit": "seconds",
            "snapshot_created_at": allocation.get("created_at") if allocation else None,
            "source_age_hours": source_age_hours,
            "capacity_k": (
                allocation.get("capacity_k")
                if allocation
                else configured_capacity_k()
            ),
            "scheduled_today": len(allocation["selected"]) if allocation else 0,
            "exploration_slots": (
                allocation.get("exploration_slots", 0) if allocation else 0
            ),
            "view_source_available": source_available,
            "classification_basis": classification_basis,
            "total": total,
            "items": items[:limit],
        }
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")