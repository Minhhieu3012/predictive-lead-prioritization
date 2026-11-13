import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

VN_TZ = timezone(timedelta(hours=7))

ENQUIRIES_PATH = Path("data/raw/enquiries_snapshot.csv")
OUTCOMES_PATH = Path("data/raw/outcome_events.csv")
QUARANTINE_PATH = Path("data/quarantine/quarantine_rows.csv")
DB_PATH = Path("data/songnhat.db")

REQUIRED_ENQ_COLS = [
    "enquiry_id", "account_key", "t0_time", "extraction_time", "product_family"
]
REQUIRED_OUT_COLS = ["enquiry_id", "event_type", "event_time", "label", "cohort"]
VALID_PRODUCT_FAMILIES = {"Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"}


def parse_vn_iso(value: str) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt.astimezone(VN_TZ).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_enquiries (
            enquiry_id TEXT PRIMARY KEY,
            account_key TEXT NOT NULL,
            cohort TEXT,
            source TEXT,
            t0_time TEXT NOT NULL,
            extraction_time TEXT NOT NULL,
            product_family TEXT NOT NULL,
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
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_outcomes (
            enquiry_id TEXT PRIMARY KEY,
            event_type TEXT,
            event_time TEXT,
            recorded_at TEXT,
            qualification_evidence TEXT,
            business_need_confirmed TEXT,
            product_fit_confirmed TEXT,
            agreed_next_step_confirmed TEXT,
            observer_coverage TEXT,
            observation_window_days INTEGER,
            maturity_state TEXT,
            label TEXT,
            correction_status TEXT,
            cohort TEXT
        )
    """)
    conn.commit()


def validate_and_load() -> None:
    if not ENQUIRIES_PATH.exists() or not OUTCOMES_PATH.exists():
        raise FileNotFoundError("Không tìm thấy file CSV trong data/raw/.")

    enq_df = pd.read_csv(ENQUIRIES_PATH, dtype=str).fillna("")
    out_df = pd.read_csv(OUTCOMES_PATH, dtype=str).fillna("")

    missing_enq = [c for c in REQUIRED_ENQ_COLS if c not in enq_df.columns]
    missing_out = [c for c in REQUIRED_OUT_COLS if c not in out_df.columns]
    if missing_enq or missing_out:
        raise ValueError(f"Thiếu cột bắt buộc! Enquiries={missing_enq}, Outcomes={missing_out}")

    valid_enq_rows = []
    quarantine_rows = []
    bad_ids = set()

    for _, row in enq_df.iterrows():
        r = row.to_dict()
        reasons = []

        enq_id = str(r.get("enquiry_id", "")).strip()
        acct_key = str(r.get("account_key", "")).strip()
        t0_iso = parse_vn_iso(r.get("t0_time", ""))
        ext_iso = parse_vn_iso(r.get("extraction_time", ""))
        family = str(r.get("product_family", "")).strip()

        if not enq_id:
            reasons.append("Thieu enquiry_id")
        if not acct_key:
            reasons.append("Thieu account_key")
        if t0_iso is None:
            reasons.append("Thieu hoac sai dinh dang t0_time")
        if ext_iso is None:
            reasons.append("Thieu hoac sai dinh dang extraction_time")
        if family not in VALID_PRODUCT_FAMILIES:
            reasons.append(f"Sai product_family ({family})")

        if reasons:
            r["quarantine_reason"] = "; ".join(reasons)
            quarantine_rows.append(r)
            if enq_id:
                bad_ids.add(enq_id)
            continue

        # Chỉ nạp các cột an toàn, tự động bỏ qua 3 cột *_DO_NOT_USE để chống rò rỉ
        valid_enq_rows.append((
            enq_id,
            acct_key,
            str(r.get("cohort", "")).strip(),
            str(r.get("source", "")).strip(),
            t0_iso,
            ext_iso,
            family,
            str(r.get("spec_completeness", "")).strip(),
            str(r.get("is_urgent_replacement", "")).strip(),
            str(r.get("contract_due_date", "")).strip(),
            str(r.get("workflow_state", "")).strip(),
            str(r.get("industry_at_t0", "")).strip(),
            str(r.get("enquiry_channel_at_t0", "")).strip(),
            str(r.get("region_at_t0", "")).strip(),
            int(float(r["account_age_days_at_t0"])) if str(r.get("account_age_days_at_t0", "")).strip() else None,
            int(float(r["prior_purchase_count_at_t0"])) if str(r.get("prior_purchase_count_at_t0", "")).strip() else None,
            str(r.get("estimated_value_band_at_t0", "")).strip(),
            parse_vn_iso(r.get("feature_event_time", "")) or "",
            parse_vn_iso(r.get("feature_available_at", "")) or "",
        ))

    # Lọc các dòng outcome tương ứng với enquiry hợp lệ
    valid_out_rows = []
    for _, row in out_df.iterrows():
        r = row.to_dict()
        enq_id = str(r.get("enquiry_id", "")).strip()
        if not enq_id or enq_id in bad_ids:
            continue
        obs_days = str(r.get("observation_window_days", "")).strip()
        valid_out_rows.append((
            enq_id,
            str(r.get("event_type", "")).strip(),
            parse_vn_iso(r.get("event_time", "")) or str(r.get("event_time", "")).strip(),
            parse_vn_iso(r.get("recorded_at", "")) or str(r.get("recorded_at", "")).strip(),
            str(r.get("qualification_evidence", "")).strip(),
            str(r.get("business_need_confirmed", "")).strip(),
            str(r.get("product_fit_confirmed", "")).strip(),
            str(r.get("agreed_next_step_confirmed", "")).strip(),
            str(r.get("observer_coverage", "")).strip(),
            int(float(obs_days)) if obs_days else None,
            str(r.get("maturity_state", "")).strip(),
            str(r.get("label", "")).strip(),
            str(r.get("correction_status", "")).strip(),
            str(r.get("cohort", "")).strip(),
        ))

    # Ghi các dòng lỗi ra data/quarantine/quarantine_rows.csv
    QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if quarantine_rows:
        pd.DataFrame(quarantine_rows).to_csv(QUARANTINE_PATH, index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=list(enq_df.columns) + ["quarantine_reason"]).to_csv(
            QUARANTINE_PATH, index=False, encoding="utf-8"
        )

    # Lưu vào SQLite dùng INSERT OR IGNORE theo enquiry_id để chạy lại không bị trùng
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_enquiries (
                enquiry_id, account_key, cohort, source, t0_time, extraction_time,
                product_family, spec_completeness, is_urgent_replacement,
                contract_due_date, workflow_state, industry_at_t0,
                enquiry_channel_at_t0, region_at_t0, account_age_days_at_t0,
                prior_purchase_count_at_t0, estimated_value_band_at_t0,
                feature_event_time, feature_available_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            valid_enq_rows,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO raw_outcomes (
                enquiry_id, event_type, event_time, recorded_at,
                qualification_evidence, business_need_confirmed,
                product_fit_confirmed, agreed_next_step_confirmed,
                observer_coverage, observation_window_days, maturity_state,
                label, correction_status, cohort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            valid_out_rows,
        )
        conn.commit()

        db_enq_count = conn.execute("SELECT COUNT(*) FROM raw_enquiries").fetchone()[0]
        db_out_count = conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0]

    print("=" * 60)
    print("US-06 | KET QUA KIEM TRA & NAP DU LIEU CSV")
    print("=" * 60)
    print(f"Tong so dong doc (enquiries_snapshot.csv) : {len(enq_df)}")
    print(f"So dong hop le da kiem tra                 : {len(valid_enq_rows)}")
    print(f"So dong loi dua vao Quarantine             : {len(quarantine_rows)}")
    print("-" * 60)
    print(f"Tong so dong trong DB (raw_enquiries)      : {db_enq_count}")
    print(f"Tong so dong trong DB (raw_outcomes)       : {db_out_count}")
    print(f"File loi da ghi tai                        : {QUARANTINE_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    validate_and_load()