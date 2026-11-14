"""PLPT-16: FastAPI Queue Endpoint (khớp Bộ quy tắc Khóa cứng mục 6)."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.rules.rules_engine import RULES_LABEL, _parse_datetime, build_queue

VN_TZ = timezone(timedelta(hours=7))

app = FastAPI(
    title="SongNhat AI-02 Predictive Lead Prioritization API",
    version="1.0.0",
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


def load_enquiries_and_meta() -> tuple[list[dict], set[tuple[str, str]], float]:
    """Đọc bảng clean_enquiries, danh sách chặn (nếu có) và tuổi dữ liệu."""
    db_path = _get_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM clean_enquiries")
        enquiries = [dict(row) for row in cursor.fetchall()]

        # Kiểm tra bảng blocked_accounts (nếu Người 9 đã tạo)
        blocked_set: set[tuple[str, str]] = set()
        has_blocked_table = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='blocked_accounts'"
        ).fetchone()
        if has_blocked_table:
            for row in cursor.execute(
                "SELECT DISTINCT enquiry_id, account_key FROM blocked_accounts"
            ).fetchall():
                blocked_set.add((str(row["enquiry_id"]), str(row["account_key"])))

        # Tính source_age_hours dựa trên extraction_time mới nhất
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

        return enquiries, blocked_set, age_hours
    finally:
        connection.close()


@app.get("/api/queue")
def get_queue(
    limit: int = Query(default=100, ge=1, le=1000),
):
    """
    Trả về danh sách xếp hạng theo đúng định dạng khóa cứng mục 6.
    """
    try:
        enquiries, blocked_set, source_age_hours = load_enquiries_and_meta()
        now_vn = datetime.now(VN_TZ)
        queue = build_queue(enquiries, now=now_vn)

        status = "HOLD_STALE_DATA" if source_age_hours > 24.0 else "OK"

        items = []
        for entry in queue[:limit]:
            enq_id = str(entry.get("enquiry_id") or "")
            acct_key = str(entry.get("account_key") or "")
            items.append({
                "enquiry_id": enq_id,
                "account_key": acct_key,
                "final_rank": int(entry["queue_rank"]),
                "rule_score": float(entry["rules_score"]),
                "score_mode": RULES_LABEL,
                "is_urgent": bool(entry["tier0_bypass"]),
                "is_blocked": (enq_id, acct_key) in blocked_set,
                "reasons": entry["reasons"],
                "t0_time": str(entry.get("t0_time") or ""),
            })

        return {
            "status": status,
            "source_age_hours": source_age_hours,
            "items": items,
        }

    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error))
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Database error: {error}")