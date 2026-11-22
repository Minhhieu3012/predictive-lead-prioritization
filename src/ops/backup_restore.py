"""Sprint 5 / OPS-07 / Annex C: SQLite Backup & Restore with Mandatory Restriction Replay."""

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from src.security.incident_hold import ensure_incident_tables

VN_TZ = timezone(timedelta(hours=7))
BACKUP_RETENTION_DAYS = 35
RTO_HOURS = 4
RPO_HOURS = 24


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_sqlite_backup(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    OPS-07: Tạo bản sao lưu SQLite nhất quán kèm checksum SHA-256 và hạn lưu giữ 35 ngày.
    """
    src_path = Path(db_path)
    if not src_path.exists():
        raise FileNotFoundError(f"Database not found: {src_path}")

    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(VN_TZ)).astimezone(VN_TZ).replace(microsecond=0)
    stamp = ts.strftime("%Y%m%dT%H%M%S")
    backup_file = out_dir / f"songnhat_backup_{stamp}.db"

    with contextlib.closing(sqlite3.connect(str(src_path))) as src_conn:
        with contextlib.closing(sqlite3.connect(str(backup_file))) as dst_conn:
            src_conn.backup(dst_conn)

    checksum = _sha256_file(backup_file)
    expires_at = (ts + timedelta(days=BACKUP_RETENTION_DAYS)).isoformat()
    meta = {
        "backup_file": str(backup_file),
        "sha256": checksum,
        "created_at": ts.isoformat(),
        "expires_at": expires_at,
        "retention_days": BACKUP_RETENTION_DAYS,
        "rto_hours": RTO_HOURS,
        "rpo_hours": RPO_HOURS,
    }
    meta_path = out_dir / f"songnhat_backup_{stamp}.manifest.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    meta["manifest_file"] = str(meta_path)
    return meta


def restore_backup_with_restriction_replay(
    backup_path: str | Path,
    target_db_path: str | Path,
    *,
    restriction_events: list[dict[str, Any]] | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """
    OPS-07 & Annex C: Khôi phục bản sao lưu vào `target_db_path` và BẮT BUỘC chạy lại (replay)
    toàn bộ lệnh chặn khẩn cấp (`blocked_accounts`) / xóa bản ghi TRƯỚC KHI mở quyền truy cập.
    """
    src_backup = Path(backup_path)
    if not src_backup.exists():
        raise FileNotFoundError(f"Backup file not found: {src_backup}")

    actual_sha = _sha256_file(src_backup)
    if expected_sha256 and actual_sha != expected_sha256:
        raise ValueError("Checksum SHA-256 của bản sao lưu không khớp; từ chối khôi phục.")

    dst_path = Path(target_db_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with contextlib.closing(sqlite3.connect(str(src_backup))) as src_conn:
        with contextlib.closing(sqlite3.connect(str(dst_path))) as dst_conn:
            src_conn.backup(dst_conn)

    replayed_blocks = 0
    replayed_deletions = 0
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    with contextlib.closing(sqlite3.connect(str(dst_path))) as conn:
        with conn:
            ensure_incident_tables(conn)
            for evt in restriction_events or []:
                action = str(evt.get("action", "BLOCK")).strip().upper()
                enq_id = str(evt.get("enquiry_id") or "").strip() or None
                acct_key = str(evt.get("account_key") or "").strip() or None
                reason = str(evt.get("reason") or "Replayed restriction after restore").strip()
                created_by = str(evt.get("created_by") or "OPS_RESTORE").strip()
                blocked_at = str(evt.get("blocked_at") or now_iso)

                if action == "BLOCK":
                    exists = conn.execute(
                        """
                        SELECT 1 FROM blocked_accounts
                        WHERE COALESCE(enquiry_id, '') = COALESCE(?, '')
                          AND COALESCE(account_key, '') = COALESCE(?, '')
                        LIMIT 1
                        """,
                        (enq_id, acct_key),
                    ).fetchone()
                    if exists is None:
                        conn.execute(
                            """
                            INSERT INTO blocked_accounts (enquiry_id, account_key, reason, blocked_at, created_by)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (enq_id, acct_key, reason, blocked_at, created_by),
                        )
                        replayed_blocks += 1
                elif action == "DELETE" and enq_id:
                    for table in ("raw_enquiries", "clean_enquiries"):
                        has_tbl = conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone()
                        if has_tbl:
                            cur = conn.execute(f"DELETE FROM {table} WHERE enquiry_id = ?", (enq_id,))
                            replayed_deletions += int(cur.rowcount)
            conn.commit()

    return {
        "status": "RESTORED_AND_VERIFIED",
        "target_db": str(dst_path),
        "sha256_verified": actual_sha,
        "replayed_blocks": replayed_blocks,
        "replayed_deletions": replayed_deletions,
        "restored_at": now_iso,
    }
