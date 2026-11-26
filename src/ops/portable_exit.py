"""Sprint 5 / OPS-08 / DEL-04: Portable Exit Export (Neutral CSV/JSON Handover Bundle)."""

import contextlib
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

EXPORTABLE_TABLES = (
    "clean_enquiries",
    "raw_outcomes",
    "merge_audit_log",
    "blocked_accounts",
    "staff_review_logs",
    "staff_override_logs",
    "shadow_score_logs",
)


def export_portable_exit_bundle(
    db_path: str | Path,
    export_dir: str | Path,
    *,
    tables: tuple[str, ...] = EXPORTABLE_TABLES,
) -> dict[str, Any]:
    """
    OPS-08 & DEL-04: Xuất toàn bộ dữ liệu được phép, cấu trúc bảng (DDL) và biên bản bàn giao
    ra định dạng trung lập (UTF-8 CSV + JSON) để SongNhat có thể kiểm đọc độc lập khi rời bỏ hệ thống.
    Tự động lọc bỏ mọi cột kết thúc bằng `_DO_NOT_USE`.
    """
    src_db = Path(db_path)
    if not src_db.exists():
        raise FileNotFoundError(f"Database not found: {src_db}")

    out_dir = Path(export_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported_files: dict[str, dict[str, Any]] = {}
    ddl_map: dict[str, str] = {}

    with contextlib.closing(sqlite3.connect(str(src_db))) as conn:
        with conn:
            existing_tables = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
                ).fetchall()
            }

            for table in tables:
                if table not in existing_tables:
                    continue
                ddl_map[table] = existing_tables[table]
                df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
                safe_cols = [c for c in df.columns if not str(c).endswith("_DO_NOT_USE")]
                safe_df = df[safe_cols]
                csv_path = out_dir / f"{table}.csv"
                safe_df.to_csv(csv_path, index=False, encoding="utf-8")
                exported_files[table] = {
                    "file": csv_path.name,
                    "row_count": int(len(safe_df)),
                    "columns": list(safe_cols),
                }

    schema_path = out_dir / "schema_ddl.json"
    schema_path.write_text(json.dumps(ddl_map, indent=2, ensure_ascii=False), encoding="utf-8")

    manifest = {
        "exported_at": datetime.now(VN_TZ).replace(microsecond=0).isoformat(),
        "source_db": str(src_db),
        "format": "UTF-8 CSV + JSON (Neutral Portable Exit)",
        "tables": exported_files,
        "schema_file": schema_path.name,
    }
    manifest_path = out_dir / "exit_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest_file"] = str(manifest_path)
    return manifest

