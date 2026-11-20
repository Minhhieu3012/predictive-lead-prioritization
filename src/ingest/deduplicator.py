"""PLPT-15 & CF-09: Multi-Channel Identity Deduplication Engine + Steward Review Queue."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import contextlib
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


def _split_60d_windows(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    start_t0: datetime | None = None
    for row in items:
        row_t0 = _parse_iso(row.get("t0_time"))
        if not current:
            current = [row]
            start_t0 = row_t0
        else:
            if (
                start_t0 is not None
                and start_t0 != datetime.max.replace(tzinfo=VN_TZ)
                and row_t0 != datetime.max.replace(tzinfo=VN_TZ)
                and (row_t0 - start_t0).total_seconds() > WINDOW_DAYS * 86400
            ):
                windows.append(current)
                current = [row]
                start_t0 = row_t0
            else:
                current.append(row)
    if current:
        windows.append(current)
    return windows


def _detect_cluster_conflict(items: list[dict[str, Any]]) -> str | None:
    """
    CF-09: Phát hiện xung đột thuộc tính trong cùng nhóm ứng viên gộp trùng:
    - Xung đột is_urgent_replacement (ví dụ Urgent vs Normal)
    - Xung đột spec_completeness (Full vs Minimal)
    """
    urg_vals = {
        str(r.get("is_urgent_replacement") or "").strip().lower()
        for r in items
        if not _is_empty(r.get("is_urgent_replacement"))
    }
    if len(urg_vals) > 1 and ("urgent" in urg_vals):
        return "Conflicting is_urgent_replacement within 60-day window"

    spec_vals = {
        str(r.get("spec_completeness") or "").strip().lower()
        for r in items
        if not _is_empty(r.get("spec_completeness"))
    }
    if "full" in spec_vals and "minimal" in spec_vals:
        return "Conflicting spec_completeness (Full vs Minimal) within 60-day window"

    return None


def deduplicate_dataframe_with_steward(
    raw_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Hợp nhất định danh đa kênh (PLPT-15 + CF-09):
    - Không gộp hồ sơ đang mở vào hồ sơ Closed/withdrawn.
    - Hai hồ sơ đang mở cùng (account_key, product_family) trong <= 60 ngày:
      + Khác project_ref_code (cả 2 đều khác rỗng) -> giữ 2 hồ sơ độc lập.
      + Một bên thiếu project_ref_code (một bên có, một bên rỗng) -> đưa vào steward_review_queue (DATA_STEWARD), không tự gộp.
      + Xung đột is_urgent_replacement hoặc spec_completeness (Full vs Minimal) -> đưa vào steward_review_queue (DATA_STEWARD), không tự gộp.
      + Khác kênh và khớp khóa, không xung đột -> gộp theo CF-07.
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
    steward_columns = [
        "enquiry_id_a",
        "enquiry_id_b",
        "account_key",
        "product_family",
        "conflict_reason",
        "status",
        "created_at",
    ]

    if raw_df.empty:
        return (
            raw_df.copy(),
            pd.DataFrame(columns=audit_columns),
            pd.DataFrame(columns=steward_columns),
        )

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

    # Nhóm trước theo (account_key, product_family)
    acct_prod_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in active_records:
        acct = str(row.get("account_key") or "").strip()
        prod = str(row.get("product_family") or "").strip()
        acct_prod_groups.setdefault((acct, prod), []).append(row)

    clean_records: list[dict[str, Any]] = list(inactive_records)
    audit_rows: list[dict[str, Any]] = []
    steward_rows: list[dict[str, Any]] = []
    now_iso = datetime.now(VN_TZ).isoformat(timespec="seconds")

    for (acct, prod), group_items in acct_prod_groups.items():
        windows = _split_60d_windows(group_items)
        for window_items in windows:
            if len(window_items) == 1:
                clean_records.append(dict(window_items[0]))
                continue

            proj_values = [
                str(r.get("project_ref_code") or "").strip()
                if (has_project_ref and not _is_empty(r.get("project_ref_code")))
                else ""
                for r in window_items
            ]
            has_non_empty_proj = any(p != "" for p in proj_values)
            has_empty_proj = any(p == "" for p in proj_values)

            # CF-09: Một bên có project_ref_code, một bên thiếu trong cùng 60 ngày -> DATA_STEWARD
            if has_non_empty_proj and has_empty_proj:
                anchor = window_items[0]
                for other in window_items[1:]:
                    steward_rows.append(
                        {
                            "enquiry_id_a": str(anchor.get("enquiry_id") or ""),
                            "enquiry_id_b": str(other.get("enquiry_id") or ""),
                            "account_key": acct,
                            "product_family": prod,
                            "conflict_reason": "Missing project_ref_code on one side within 60-day window",
                            "status": "DATA_STEWARD",
                            "created_at": now_iso,
                        }
                    )
                for r in window_items:
                    clean_records.append(dict(r))
                continue

            # Tách theo project_ref_code (nếu khác nhau và đều khác rỗng -> giữ độc lập)
            subgroups: dict[str, list[dict[str, Any]]] = {}
            for r, p in zip(window_items, proj_values):
                subgroups.setdefault(p, []).append(r)

            for proj, items in subgroups.items():
                if len(items) == 1:
                    clean_records.append(dict(items[0]))
                    continue

                conflict_reason = _detect_cluster_conflict(items)
                if conflict_reason is not None:
                    anchor = items[0]
                    for other in items[1:]:
                        steward_rows.append(
                            {
                                "enquiry_id_a": str(anchor.get("enquiry_id") or ""),
                                "enquiry_id_b": str(other.get("enquiry_id") or ""),
                                "account_key": acct,
                                "product_family": prod,
                                "conflict_reason": conflict_reason,
                                "status": "DATA_STEWARD",
                                "created_at": now_iso,
                            }
                        )
                    for r in items:
                        clean_records.append(dict(r))
                    continue

                # Không xung đột -> gộp theo CF-07
                canonical = dict(items[0])
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
    steward_df = pd.DataFrame(steward_rows, columns=steward_columns)
    return clean_df, merge_logs_df, steward_df


def deduplicate_dataframe(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Giữ nguyên chữ ký hàm tương thích ngược với bộ test cũ."""
    clean_df, merge_logs_df, _ = deduplicate_dataframe_with_steward(raw_df)
    return clean_df, merge_logs_df


def init_dedup_tables(conn: sqlite3.Connection) -> None:
    """
    Khởi tạo bảng clean_enquiries (giữ nguyên PK & schema),
    bảng merge_audit_log, VIEW merge_logs và bảng steward_review_queue (CF-09).
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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS steward_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id_a TEXT NOT NULL,
            enquiry_id_b TEXT NOT NULL,
            account_key TEXT NOT NULL,
            product_family TEXT NOT NULL,
            conflict_reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DATA_STEWARD',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def deduplicate_enquiries(db_path: str | Path | None = None) -> dict[str, Any]:
    """
    Đọc raw_enquiries từ SQLite, chạy deduplicate_dataframe_with_steward,
    cập nhật clean_enquiries, merge_audit_log và steward_review_queue trong một transaction BEGIN IMMEDIATE.
    """
    target_db = str(db_path or _default_db_path())

    with contextlib.closing(sqlite3.connect(target_db)) as conn:
        with conn:
            init_dedup_tables(conn)
            raw_df = pd.read_sql_query("SELECT * FROM raw_enquiries", conn)
            clean_df, merge_logs_df, steward_df = deduplicate_dataframe_with_steward(raw_df)

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
                merge_logs_df.to_sql("merge_audit_log", conn, if_exists="append", index=False)

            conn.execute("DELETE FROM steward_review_queue")
            if not steward_df.empty:
                steward_df.to_sql("steward_review_queue", conn, if_exists="append", index=False)
            conn.commit()

            clean_count = int(len(clean_df))
            raw_count = int(len(raw_df))
            dup_count = int(len(merge_logs_df))
            steward_count = int(len(steward_df))
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
        "steward_review_count": steward_count,
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
        f"merged={summary['duplicates_count']}, "
        f"steward_review={summary['steward_review_count']}"
    )
