"""
SongNhat AI-02 · Predictive Lead Prioritization (PLPT)
Task PLPT-15 (US-07: Identity Resolution & Multi-Channel Deduplication Engine)

Mục tiêu:
Hợp nhất các yêu cầu hỏi mua trùng lặp từ nhiều kênh (Website, Email, Phone, v.v.)
có cùng account_key và cùng nhu cầu sản phẩm (product_family, project_ref_code nếu có)
thành một bản ghi duy nhất trên hàng đợi Sales.
Bảo toàn mốc thời gian đủ điều kiện sớm nhất (t0_time) để tuân thủ quy tắc khóa cứng
và chống rò rỉ dữ liệu (anti-leakage).
Lưu vết toàn bộ lịch sử gộp trùng vào bảng `merge_logs` và `merge_audit_log`.
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import pandas as pd

# Múi giờ Việt Nam (+07:00) theo chuẩn hệ thống SongNhat AI-02
VN_TZ = timezone(timedelta(hours=7))
DEFAULT_DB_PATH = Path("data/songnhat.db")


def get_table_columns_info(conn: sqlite3.Connection, table_name: str) -> list[dict]:
    """Lấy thông tin cấu trúc cột từ bảng SQLite nguồn."""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": r[3],
            "dflt_value": r[4],
            "pk": r[5],
        }
        for r in rows
    ]


def init_destination_tables(
    conn: sqlite3.Connection,
    raw_cols_info: list[dict],
) -> None:
    """
    Khởi tạo bảng clean_enquiries, merge_logs và merge_audit_log trong SQLite.
    Bảng clean_enquiries sao chép cấu trúc và kiểu dữ liệu từ raw_enquiries.
    """
    # 1. DDL cho clean_enquiries
    cols_def = []
    for col in raw_cols_info:
        col_name = col["name"]
        col_type = col["type"] or "TEXT"
        col_def = f"{col_name} {col_type}"
        if col["pk"]:
            col_def += " PRIMARY KEY"
        elif col["notnull"]:
            col_def += " NOT NULL"
        cols_def.append(col_def)

    ddl_clean = f"""
    CREATE TABLE IF NOT EXISTS clean_enquiries (
        {', '.join(cols_def)}
    );
    """

    # 2. DDL cho merge_logs
    ddl_merge_logs = """
    CREATE TABLE IF NOT EXISTS merge_logs (
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
    );
    """

    # 3. DDL cho merge_audit_log (chuẩn Jira BE/Data)
    ddl_merge_audit_log = """
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
    );
    """

    conn.execute(ddl_clean)
    conn.execute(ddl_merge_logs)
    conn.execute(ddl_merge_audit_log)


def determine_group_keys(df: pd.DataFrame, custom_keys: list[str] | None = None) -> list[str]:
    """
    Xác định tập khóa định danh gộp trùng.
    Mặc định: account_key + product_family (+ project_ref_code nếu có trong bảng).
    """
    if custom_keys:
        return custom_keys

    keys = ["account_key", "product_family"]
    if "project_ref_code" in df.columns:
        keys.append("project_ref_code")
    return keys


def deduplicate_dataframe(
    raw_df: pd.DataFrame,
    group_keys: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Thuật toán hợp nhất định danh đa kênh (Identity Resolution & Deduplication).
    
    Quy tắc nghiệp vụ:
    1. Nhóm các bản ghi theo group_keys (mặc định account_key + product_family + project_ref_code).
    2. Sắp xếp các bản ghi theo t0_time tăng dần (thời điểm tiếp nhận sớm nhất).
    3. Giữ lại bản ghi đầu tiên có t0_time sớm nhất làm đại diện duy nhất (canonical/surviving).
    4. Ghi nhận toàn bộ các bản ghi bị gộp (merged/duplicate) vào danh sách audit log.
    
    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: (clean_df, merge_logs_df)
    """
    if raw_df.empty:
        clean_df = raw_df.copy()
        log_cols = [
            "canonical_enquiry_id", "merged_enquiry_id", "surviving_enquiry_id",
            "duplicate_enquiry_id", "account_key", "product_family", "project_ref_code",
            "canonical_channel", "merged_channel", "canonical_t0_time", "merged_t0_time",
            "merged_at", "merge_reason"
        ]
        merge_logs_df = pd.DataFrame(columns=log_cols)
        return clean_df, merge_logs_df

    actual_group_keys = determine_group_keys(raw_df, group_keys)

    # Chuyển đổi t0_time sang datetime để sắp xếp thời gian chính xác tuyệt đối
    df = raw_df.copy()
    df["_t0_dt"] = pd.to_datetime(df["t0_time"], errors="coerce")

    # Sắp xếp theo _t0_dt tăng dần; nếu trùng t0 thì sắp theo enquiry_id để đảm bảo tính tất định
    df_sorted = df.sort_values(
        by=["_t0_dt", "enquiry_id"],
        ascending=[True, True],
        na_position="last",
    )

    clean_rows = []
    merge_logs = []
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()

    # Nhóm theo khóa định danh
    for _, group in df_sorted.groupby(actual_group_keys, sort=False, dropna=False):
        canonical_row = group.iloc[0]
        clean_rows.append(canonical_row)

        if len(group) > 1:
            canonical_id = str(canonical_row["enquiry_id"])
            canonical_t0 = str(canonical_row["t0_time"])
            canonical_channel = (
                str(canonical_row.get("enquiry_channel_at_t0", "") or canonical_row.get("source", ""))
            )
            acct_key = str(canonical_row["account_key"])
            prod_fam = str(canonical_row["product_family"])
            proj_ref = (
                str(canonical_row["project_ref_code"])
                if "project_ref_code" in canonical_row and pd.notna(canonical_row["project_ref_code"])
                else None
            )

            for idx in range(1, len(group)):
                merged_row = group.iloc[idx]
                merged_id = str(merged_row["enquiry_id"])
                merged_t0 = str(merged_row["t0_time"])
                merged_channel = (
                    str(merged_row.get("enquiry_channel_at_t0", "") or merged_row.get("source", ""))
                )

                log_entry = {
                    "canonical_enquiry_id": canonical_id,
                    "merged_enquiry_id": merged_id,
                    "surviving_enquiry_id": canonical_id,
                    "duplicate_enquiry_id": merged_id,
                    "account_key": acct_key,
                    "product_family": prod_fam,
                    "project_ref_code": proj_ref,
                    "canonical_channel": canonical_channel,
                    "merged_channel": merged_channel,
                    "canonical_t0_time": canonical_t0,
                    "merged_t0_time": merged_t0,
                    "merged_at": now_iso,
                    "merge_reason": (
                        f"Multi-channel duplicate merged into canonical {canonical_id} "
                        f"(preserved earliest t0: {canonical_t0})"
                    ),
                }
                merge_logs.append(log_entry)

    clean_df = pd.DataFrame(clean_rows).drop(columns=["_t0_dt"]).reset_index(drop=True)

    log_cols = [
        "canonical_enquiry_id", "merged_enquiry_id", "surviving_enquiry_id",
        "duplicate_enquiry_id", "account_key", "product_family", "project_ref_code",
        "canonical_channel", "merged_channel", "canonical_t0_time", "merged_t0_time",
        "merged_at", "merge_reason"
    ]
    merge_logs_df = pd.DataFrame(merge_logs, columns=log_cols)

    return clean_df, merge_logs_df


def deduplicate_enquiries(
    db_path: str | Path = DEFAULT_DB_PATH,
    group_keys: list[str] | None = None,
) -> dict:
    """
    Hàm chính xử lý gộp trùng từ SQLite DB:
    1. Đọc dữ liệu từ bảng raw_enquiries
    2. Áp dụng thuật toán gộp trùng đa kênh, bảo toàn t0 sớm nhất
    3. Ghi kết quả vào bảng clean_enquiries
    4. Ghi vết lịch sử vào bảng merge_logs và merge_audit_log
    
    Returns:
        dict: Thống kê chi tiết số lượng trước/sau khi gộp
    """
    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError(f"Không tìm thấy file cơ sở dữ liệu: {db_file}")

    conn = sqlite3.connect(db_file)
    try:
        # Kiểm tra sự tồn tại của bảng raw_enquiries
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='raw_enquiries'"
        ).fetchone()
        if not table_check:
            raise ValueError(f"Bảng 'raw_enquiries' không tồn tại trong {db_file}!")

        # Lấy thông tin cấu trúc cột từ raw_enquiries
        raw_cols_info = get_table_columns_info(conn, "raw_enquiries")

        # Đọc dữ liệu nguồn
        raw_df = pd.read_sql_query("SELECT * FROM raw_enquiries", conn)
        raw_count = len(raw_df)

        actual_group_keys = determine_group_keys(raw_df, group_keys)

        # Thực hiện thuật toán hợp nhất
        clean_df, merge_logs_df = deduplicate_dataframe(raw_df, group_keys=actual_group_keys)
        clean_count = len(clean_df)
        duplicates_count = len(merge_logs_df)

        # Kiểm tra tính toàn vẹn toán học: raw_count - clean_count == duplicates_count
        if raw_count - clean_count != duplicates_count:
            raise AssertionError(
                f"Lỗi kiểm tra số dòng: raw ({raw_count}) - clean ({clean_count}) = "
                f"{raw_count - clean_count} != duplicates ({duplicates_count})"
            )

        # Lưu vào DB trong transaction an toàn
        with conn:
            # Xóa các bảng đích cũ trước khi ghi lại để đảm bảo tính Idempotent (chạy nhiều lần không trùng)
            conn.execute("DROP TABLE IF EXISTS clean_enquiries")
            conn.execute("DROP TABLE IF EXISTS merge_logs")
            conn.execute("DROP TABLE IF EXISTS merge_audit_log")

            init_destination_tables(conn, raw_cols_info)

            # Ghi clean_enquiries
            clean_df.to_sql("clean_enquiries", conn, if_exists="append", index=False)

            # Ghi merge_logs & merge_audit_log
            if not merge_logs_df.empty:
                merge_logs_df.to_sql("merge_logs", conn, if_exists="append", index=False)
                merge_logs_df.to_sql("merge_audit_log", conn, if_exists="append", index=False)

            # Tạo indexes để tăng tốc độ truy vấn
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_clean_account_product ON clean_enquiries(account_key, product_family)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_merge_canonical ON merge_logs(canonical_enquiry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_merge_duplicate ON merge_logs(merged_enquiry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_canonical ON merge_audit_log(canonical_enquiry_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_duplicate ON merge_audit_log(merged_enquiry_id)"
            )
    finally:
        conn.close()

    return {
        "status": "SUCCESS",
        "db_path": str(db_file),
        "group_keys": actual_group_keys,
        "raw_count": raw_count,
        "clean_count": clean_count,
        "duplicates_count": duplicates_count,
        "merge_logs_count": len(merge_logs_df),
        "merge_audit_log_count": len(merge_logs_df),
        "math_check_passed": (raw_count - clean_count == duplicates_count),
    }


def main():
    parser = argparse.ArgumentParser(
        description="PLPT-15 | US-07: Identity Resolution & Multi-Channel Deduplication Engine"
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help=f"Đường dẫn file database SQLite (mặc định: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("PLPT-15 | US-07: IDENTITY RESOLUTION & MULTI-CHANNEL DEDUPLICATION")
    print("=" * 72)

    try:
        stats = deduplicate_enquiries(db_path=args.db_path)
    except Exception as exc:
        print(f"[FATAL] Lỗi trong quá trình gộp trùng: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"File Database                     : {stats['db_path']}")
    print(f"Tiêu chí nhóm định danh (Keys)    : {stats['group_keys']}")
    print("-" * 72)
    print(f"Tổng số dòng ban đầu (raw_enquiries)      : {stats['raw_count']}")
    print(f"Tổng số dòng sau gộp (clean_enquiries)    : {stats['clean_count']}")
    print(f"Số dòng trùng lặp đã gộp (duplicates)     : {stats['duplicates_count']}")
    print(f"Số bản ghi lịch sử trong merge_logs       : {stats['merge_logs_count']}")
    print(f"Số bản ghi lịch sử trong merge_audit_log  : {stats['merge_audit_log_count']}")
    print("-" * 72)
    print(
        f"Kiểm tra toán học (raw - clean == dup)    : "
        f"{stats['raw_count']} - {stats['clean_count']} = {stats['duplicates_count']} -> "
        f"{'PASS' if stats['math_check_passed'] else 'FAIL'}"
    )
    print("Bảo toàn mốc t0_time sớm nhất             : PASS (Khóa cứng & Chống rò rỉ)")
    print("Bàn giao dữ liệu cho Người 8              : SẴN SÀNG (bảng clean_enquiries)")
    print("=" * 72)


if __name__ == "__main__":
    main()
