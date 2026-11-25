"""SEC-08 / USR-07 / OPS-05: Emergency Local Block & 4-Hour Incident Hold Response."""

import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

VN_TZ = timezone(timedelta(hours=7))
HOLD_STALE_DATA = "HOLD_STALE_DATA"
INCIDENT_NOTIFY_SLA_HOURS = 4.0
INCIDENT_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _now_vn(now: datetime | None = None) -> datetime:
    dt = now or datetime.now(VN_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ).replace(microsecond=0)


def ensure_incident_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_holds (
            incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
            hold_code TEXT NOT NULL DEFAULT 'HOLD_STALE_DATA',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            severity TEXT NOT NULL,
            affected_scope TEXT NOT NULL DEFAULT 'ALL_QUEUES',
            reason TEXT NOT NULL,
            triggered_by TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            notify_deadline_at TEXT NOT NULL,
            purchaser_notified_at TEXT,
            notified_by TEXT,
            notification_channel TEXT,
            within_4h_sla INTEGER,
            released_by TEXT,
            released_at TEXT,
            resolution_note TEXT
        )
        """
    )
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


def activate_emergency_hold(
    db_path: str | Path,
    *,
    reason: str,
    triggered_by: str,
    severity: str = "CRITICAL",
    affected_scope: str = "ALL_QUEUES",
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    SEC-08 (Giờ 0–1): Khoanh vùng khẩn cấp bằng cách khóa cổng phục vụ/xuất dữ liệu (`HOLD_STALE_DATA`)
    và mở đồng hồ SLA 4 giờ báo cáo bắt buộc cho SongNhat (Purchaser).
    """
    norm_sev = str(severity or "CRITICAL").strip().upper()
    if norm_sev not in INCIDENT_SEVERITIES:
        raise ValueError(f"Mức độ sự cố không hợp lệ: {severity}")
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("Bắt buộc ghi rõ lý do kích hoạt khóa sự cố.")

    triggered_dt = _now_vn(now)
    deadline_dt = triggered_dt + timedelta(hours=INCIDENT_NOTIFY_SLA_HOURS)

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_incident_tables(conn)
            cur = conn.execute(
                """
                INSERT INTO incident_holds (
                    hold_code, status, severity, affected_scope, reason,
                    triggered_by, triggered_at, notify_deadline_at
                ) VALUES (?, 'ACTIVE', ?, ?, ?, ?, ?, ?)
                """,
                (
                    HOLD_STALE_DATA,
                    norm_sev,
                    str(affected_scope or "ALL_QUEUES").strip(),
                    clean_reason,
                    str(triggered_by or "SYSTEM").strip(),
                    triggered_dt.isoformat(),
                    deadline_dt.isoformat(),
                ),
            )
            incident_id = int(cur.lastrowid)
            conn.commit()

    return {
        "incident_id": incident_id,
        "hold_code": HOLD_STALE_DATA,
        "status": "ACTIVE",
        "severity": norm_sev,
        "affected_scope": str(affected_scope or "ALL_QUEUES").strip(),
        "reason": clean_reason,
        "triggered_by": str(triggered_by or "SYSTEM").strip(),
        "triggered_at": triggered_dt.isoformat(),
        "notify_deadline_at": deadline_dt.isoformat(),
        "notify_sla_hours": INCIDENT_NOTIFY_SLA_HOURS,
    }


def record_purchaser_notification(
    db_path: str | Path,
    incident_id: int,
    *,
    notified_by: str,
    channel: str = "OFFICIAL_EMAIL",
    now: datetime | None = None,
) -> dict[str, Any]:
    """SEC-08 (Giờ 2–4): Ghi nhận thông báo chính thức cho SongNhat và đối chiếu SLA <= 4 giờ."""
    notified_dt = _now_vn(now)

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_incident_tables(conn)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM incident_holds WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Không tìm thấy incident_id={incident_id}")

            deadline_dt = datetime.fromisoformat(row["notify_deadline_at"])
            within_sla = notified_dt <= deadline_dt

            conn.execute(
                """
                UPDATE incident_holds
                SET purchaser_notified_at = ?, notified_by = ?, notification_channel = ?, within_4h_sla = ?
                WHERE incident_id = ?
                """,
                (
                    notified_dt.isoformat(),
                    str(notified_by or "ADMINISTRATOR").strip(),
                    str(channel or "OFFICIAL_EMAIL").strip(),
                    1 if within_sla else 0,
                    incident_id,
                ),
            )
            conn.commit()

    return {
        "incident_id": incident_id,
        "purchaser_notified_at": notified_dt.isoformat(),
        "notified_by": str(notified_by or "ADMINISTRATOR").strip(),
        "notification_channel": str(channel or "OFFICIAL_EMAIL").strip(),
        "within_4h_sla": within_sla,
    }


def release_emergency_hold(
    db_path: str | Path,
    incident_id: int,
    *,
    released_by: str,
    resolution_note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mở lại hàng đợi sau khi đã khắc phục sự cố và kiểm thử chốt an toàn."""
    clean_note = str(resolution_note or "").strip()
    if not clean_note:
        raise ValueError("Bắt buộc ghi rõ biên bản khắc phục trước khi mở khóa sự cố.")
    released_dt = _now_vn(now)

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_incident_tables(conn)
            cur = conn.execute(
                """
                UPDATE incident_holds
                SET status = 'RESOLVED', released_by = ?, released_at = ?, resolution_note = ?
                WHERE incident_id = ? AND status = 'ACTIVE'
                """,
                (
                    str(released_by or "ADMINISTRATOR").strip(),
                    released_dt.isoformat(),
                    clean_note,
                    incident_id,
                ),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Không tìm thấy khóa sự cố đang ACTIVE với incident_id={incident_id}")
            conn.commit()

    return {
        "incident_id": incident_id,
        "status": "RESOLVED",
        "released_by": str(released_by or "ADMINISTRATOR").strip(),
        "released_at": released_dt.isoformat(),
        "resolution_note": clean_note,
    }


def get_active_incident_hold(db_path: str | Path) -> dict[str, Any] | None:
    """Trả về sự cố đang khóa hàng đợi mới nhất (nếu có), ngược lại trả về None."""
    p = Path(db_path)
    if not p.exists():
        return None
    with contextlib.closing(sqlite3.connect(str(p))) as conn:
        with conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='incident_holds'"
            ).fetchone()
            if not has_table:
                return None
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM incident_holds
                WHERE status = 'ACTIVE'
                ORDER BY incident_id DESC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row is not None else None


def commit_local_emergency_block(
    db_path: str | Path,
    *,
    enquiry_id: str | None = None,
    account_key: str | None = None,
    block_account: bool = False,
    reason: str = "Emergency block by operator",
    created_by: str = "PO_OPERATOR",
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    USR-07 / OPS-05: Commit trạng thái chặn khẩn cấp cục bộ vào SQLite TRƯỚC khi trả xác nhận.
    """
    enq = str(enquiry_id or "").strip()
    acct = str(account_key or "").strip()
    if not enq and not acct:
        raise ValueError("Cần cung cấp ít nhất enquiry_id hoặc account_key.")

    if enq and not block_account:
        target_enq: str | None = enq
        target_acct: str | None = None
    else:
        if not acct:
            raise ValueError("Cần cung cấp account_key khi chọn chế độ chặn toàn tài khoản.")
        target_enq = None
        target_acct = acct

    now_iso = _now_vn(now).isoformat()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            ensure_incident_tables(conn)
            if target_enq is not None:
                existing = conn.execute(
                    "SELECT enquiry_id, account_key, reason, blocked_at FROM blocked_accounts WHERE enquiry_id = ? LIMIT 1",
                    (target_enq,),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT enquiry_id, account_key, reason, blocked_at FROM blocked_accounts WHERE COALESCE(enquiry_id, '') = '' AND account_key = ? LIMIT 1",
                    (target_acct,),
                ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO blocked_accounts (enquiry_id, account_key, reason, blocked_at, created_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (target_enq, target_acct, reason, now_iso, created_by),
                )
                conn.commit()
                return {
                    "blocked": True,
                    "status": "blocked",
                    "enquiry_id": target_enq or "",
                    "account_key": target_acct or "",
                    "reason": reason,
                    "blocked_at": now_iso,
                }
            return {
                "blocked": True,
                "status": "blocked",
                "enquiry_id": existing[0] or "",
                "account_key": existing[1] or "",
                "reason": existing[2] or reason,
                "blocked_at": existing[3] or now_iso,
            }
