"""PLPT-16 & PLPT-17: FastAPI Queue Endpoint + Emergency Block & Stale Data Hold Controls."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.rules.rules_engine import RULES_LABEL, _parse_datetime, build_queue

VN_TZ = timezone(timedelta(hours=7))
STALE_HOLD_HOURS = 24.0

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