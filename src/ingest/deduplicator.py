"""PLPT-15: Multi-Channel Identity Deduplication Engine."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))
WINDOW_DAYS = 60
INACTIVE_STATES = {"closed", "withdrawn"}


def _default_db_path() -> str:
    project_root = Path(__file__).resolve().parents[2]
    return os.getenv(
        "DATABASE_PATH",
        str(project_root / "data" / "songnhat.db"),
    )


def _is_empty(val: Any) -> bool:
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return s == "" or s.lower() in {"none", "null", "nan", "unknown"}


def _parse_iso(ts: Any) -> datetime:
    if _is_empty(ts):
        return datetime.max.replace(tzinfo=VN_TZ)
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except ValueError:
        return datetime.max.replace(tzinfo=VN_TZ)


def _parse_iso_min(ts: Any) -> datetime:
    if _is_empty(ts):
        return datetime.min.replace(tzinfo=VN_TZ)
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except ValueError:
        return datetime.min.replace(tzinfo=VN_TZ)


def deduplicate_dataframe(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Hợp nhất định danh đa kênh trên DataFrame theo (account_key, product_family, project_ref_code):
    - Không gộp hồ sơ đang mở vào hồ sơ Closed/withdrawn (giữ nguyên Closed/withdrawn độc lập).
    - Chỉ gộp các hồ sơ đang mở trong cùng cửa sổ quan sát 60 ngày kể từ t0_time của bản ghi gốc
      (hoặc cùng project_ref_code khác rỗng).
    - Giữ bản ghi có t0_time sớm nhất làm canonical, cập nhật extraction_time = max(...)
      và workflow_state theo bản ghi mới nhất theo extraction_time.
    """
    audit_columns = [
        "canonical_enquiry_id",
        "merged_enquiry_id",
        "surviving_enquiry_id",
        "duplicate_enquiry_id",
        "account_key",
        "product_family",
        "project_ref_code",
        "canonical_channel",
        "merged_channel",
        "canonical_t0_time",
        "merged_t0_time",
        "merged_at",
        "merge_reason",
    ]

    if raw_df.empty:
        return raw_df.copy(), pd.DataFrame(columns=audit_columns)

    df = raw_df.copy()
    has_project_ref = "project_ref_code" in df.columns
    columns = list(df.columns)

    records = df.to_dict(orient="records")
    inactive_records: list[dict[str, Any]] = []
    active_records: list[dict[str, Any]] = []
    for row in records:
        state = str(row.get("workflow_state") or "").strip().lower()
        if state in INACTIVE_STATES:
            inactive_records.append(dict(row))
        else:
            active_records.append(row)

    active_records.sort(
        key=lambda r: (
            _parse_iso(r.get("t0_time")),
            str(r.get("enquiry_id") or ""),
        )
    )

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in active_records:
        acct = str(row.get("account_key") or "").strip()
        prod = str(row.get("product_family") or "").strip()
        proj = ""
        if has_project_ref and not _is_empty(row.get("project_ref_code")):
            proj = str(row.get("project_ref_code")).strip()
        groups.setdefault((acct, prod, proj), []).append(row)

    # Tách cụm theo cửa sổ quan sát 60 ngày khi không có project_ref_code khớp
    clusters: list[tuple[tuple[str, str, str], list[dict[str, Any]]]] = []
    for key, items in groups.items():
        acct, prod, proj = key
        if proj:
            clusters.append((key, items))
            continue
        current_cluster: list[dict[str, Any]] = []
        cluster_start_t0: datetime | None = None
        for row in items:
            row_t0 = _parse_iso(row.get("t0_time"))
            if not current_cluster:
                current_cluster = [row]
                cluster_start_t0 = row_t0
            else:
                if (
                    cluster_start_t0 is not None
                    and cluster_start_t0 != datetime.max.replace(tzinfo=VN_TZ)
                    and row_t0 != datetime.max.replace(tzinfo=VN_TZ)
                    and (row_t0 - cluster_start_t0).total_seconds() > WINDOW_DAYS * 86400
                ):
                    clusters.append((key, current_cluster))
                    current_cluster = [row]
                    cluster_start_t0 = row_t0
                else:
                    current_cluster.append(row)
        if current_cluster:
            clusters.append((key, current_cluster))

    clean_records: list[dict[str, Any]] = list(inactive_records)
    audit_rows: list[dict[str, Any]] = []
    now_iso = datetime.now(VN_TZ).isoformat(timespec="seconds")

    for (acct, prod, proj), items in clusters:
        canonical = dict(items[0])

        # Tìm extraction_time lớn nhất và workflow_state mới nhất theo extraction_time
        latest_item = max(
            items,
            key=lambda r: (_parse_iso_min(r.get("extraction_time")), str(r.get("enquiry_id") or "")),
        )
        if not _is_empty(latest_item.get("extraction_time")):
            canonical["extraction_time"] = latest_item.get("extraction_time")
        if not _is_empty(latest_item.get("workflow_state")):
            canonical["workflow_state"] = latest_item.get("workflow_state")

        for dup in items[1:]:
            for col in columns:
                if col in {"enquiry_id", "t0_time", "extraction_time", "workflow_state"}:
                    continue
                if _is_empty(canonical.get(col)) and not _is_empty(dup.get(col)):
                    canonical[col] = dup[col]

            if str(dup.get("is_urgent_replacement") or "").strip() == "Urgent":
                canonical["is_urgent_replacement"] = "Urgent"

            canonical_channel = (
                canonical.get("enquiry_channel_at_t0")
                if not _is_empty(canonical.get("enquiry_channel_at_t0"))
                else canonical.get("source")
            )
            merged_channel = (
                dup.get("enquiry_channel_at_t0")
                if not _is_empty(dup.get("enquiry_channel_at_t0"))
                else dup.get("source")
            )

            audit_rows.append(
                {
                    "canonical_enquiry_id": canonical["enquiry_id"],
                    "merged_enquiry_id": dup["enquiry_id"],
                    "surviving_enquiry_id": canonical["enquiry_id"],
                    "duplicate_enquiry_id": dup["enquiry_id"],
                    "account_key": acct,
                    "product_family": prod,
                    "project_ref_code": proj if proj else None,
                    "canonical_channel": canonical_channel,
                    "merged_channel": merged_channel,
                    "canonical_t0_time": canonical["t0_time"],
                    "merged_t0_time": dup["t0_time"],
                    "merged_at": now_iso,
                    "merge_reason": (
                        f"Multi-channel duplicate merged into canonical {canonical['enquiry_id']} "
                        f"(preserved earliest t0: {canonical['t0_time']})"
                    ),
                }
            )

        clean_records.append(canonical)

    clean_df = pd.DataFrame(clean_records, columns=columns)
    merge_logs_df = pd.DataFrame(audit_rows, columns=audit_columns)
    return clean_df, merge_logs_df


def init_dedup_tables(conn: sqlite3.Connection) -> None:
    """
    Khởi tạo bảng clean_enquiries (giữ nguyên PK & schema),
    bảng merge_audit_log và VIEW merge_logs.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clean_enquiries (
            enquiry_id TEXT PRIMARY KEY,
            account_key TEXT NOT NULL,
            cohort TEXT,
            source TEXT,
            t0_time TEXT NOT NULL,
            extraction_time TEXT NOT NULL,
            product_family TEXT NOT NULL,
            project_ref_code TEXT,
            spec_completeness TEXT,
            is_urgent_replacement TEXT,
            contract_due_date TEXT,
            workflow_state TEXT,
            industry_at_t0 TEXT,
            enquiry_channel_at_t0 TEXT,
            region_at_t0 TEXT,
            account_age_days_at_t0 INTEGER,
            prior_purchase_count_at_t0 INTEGER,
            estimated_value_band_at_t0 TEXT,
            feature_event_time TEXT,
            feature_available_at TEXT
        )
        """
    )
    existing_clean_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(clean_enquiries)").fetchall()
    }
    if "project_ref_code" not in existing_clean_cols:
        conn.execute("ALTER TABLE clean_enquiries ADD COLUMN project_ref_code TEXT")

    existing = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='merge_logs'"
    ).fetchone()
    if existing:
        if existing[0] == "table":
            conn.execute("DROP TABLE merge_logs")
        elif existing[0] == "view":
            conn.execute("DROP VIEW merge_logs")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS merge_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_enquiry_id TEXT NOT NULL,
            merged_enquiry_id TEXT NOT NULL,
            surviving_enquiry_id TEXT,
            duplicate_enquiry_id TEXT,
            account_key TEXT NOT NULL,
            product_family TEXT NOT NULL,
            project_ref_code TEXT,
            canonical_channel TEXT,
            merged_channel TEXT,
            canonical_t0_time TEXT NOT NULL,
            merged_t0_time TEXT NOT NULL,
            merged_at TEXT NOT NULL,
            merge_reason TEXT
        )
        """
    )
    conn.execute("CREATE VIEW merge_logs AS SELECT * FROM merge_audit_log")
    conn.commit()


def deduplicate_enquiries(db_path: str | Path | None = None) -> dict[str, Any]:
    """
    Đọc raw_enquiries từ SQLite, chạy deduplicate_dataframe,
    cập nhật clean_enquiries và merge_audit_log trong một transaction BEGIN IMMEDIATE.
    """
    target_db = str(db_path or _default_db_path())

    with sqlite3.connect(target_db) as conn:
        init_dedup_tables(conn)
        raw_df = pd.read_sql_query("SELECT * FROM raw_enquiries", conn)
        clean_df, merge_logs_df = deduplicate_dataframe(raw_df)

        # Chỉ chèn các cột có trong bảng clean_enquiries để giữ nguyên schema & PK
        table_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(clean_enquiries)").fetchall()
        ]
        for col in table_cols:
            if col not in clean_df.columns:
                clean_df[col] = None
        clean_insert_df = clean_df[table_cols]

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM clean_enquiries")
        if not clean_insert_df.empty:
            clean_insert_df.to_sql("clean_enquiries", conn, if_exists="append", index=False)

        conn.execute("DELETE FROM merge_audit_log")
        if not merge_logs_df.empty:
            merge_logs_df.to_sql(
                "merge_audit_log", conn, if_exists="append", index=False
            )
        conn.commit()

        clean_count = int(len(clean_df))
        raw_count = int(len(raw_df))
        dup_count = int(len(merge_logs_df))
        audit_count = int(
            conn.execute("SELECT COUNT(*) FROM merge_audit_log").fetchone()[0]
        )
        logs_count = int(
            conn.execute("SELECT COUNT(*) FROM merge_logs").fetchone()[0]
        )

    return {
        "status": "SUCCESS",
        "raw_count": raw_count,
        "clean_count": clean_count,
        "duplicates_count": dup_count,
        "merged_count": dup_count,
        "merge_logs_count": logs_count,
        "merge_audit_log_count": audit_count,
        "math_check_passed": (raw_count - clean_count == dup_count),
    }


def run_deduplication(db_path: str | Path | None = None) -> dict[str, Any]:
    """Alias tương thích cho các script gọi run_deduplication."""
    return deduplicate_enquiries(db_path=db_path)


if __name__ == "__main__":
    summary = deduplicate_enquiries()
    print(
        f"[PLPT-15] Deduplication complete: "
        f"raw={summary['raw_count']}, "
        f"clean={summary['clean_count']}, "
        f"merged={summary['duplicates_count']}"
    )