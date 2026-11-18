import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

VN_TZ = timezone(timedelta(hours=7))
PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENQUIRIES_PATH = PROJECT_ROOT / "data" / "raw" / "enquiries_snapshot.csv"
OUTCOMES_PATH = PROJECT_ROOT / "data" / "raw" / "outcome_events.csv"
QUARANTINE_PATH = PROJECT_ROOT / "data" / "quarantine" / "quarantine_rows.csv"
QUARANTINE_OUTCOMES_PATH = PROJECT_ROOT / "data" / "quarantine" / "quarantine_outcomes.csv"
DB_PATH = PROJECT_ROOT / "data" / "songnhat.db"

REQUIRED_ENQ_COLS = [
    "enquiry_id", "account_key", "t0_time", "extraction_time", "product_family"
]
REQUIRED_OUT_COLS = ["enquiry_id", "event_type", "event_time", "label", "cohort"]
VALID_PRODUCT_FAMILIES = {"Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"}
VALID_OUTCOME_LABELS = {"POSITIVE", "NEGATIVE", "Unknown"}
VALID_MATURITY_STATES = {"MATURE", "IMMATURE"}


def _safe_int(value: str) -> tuple[int | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, True
    try:
        return int(float(text)), True
    except (ValueError, TypeError):
        return None, False


def _is_valid_due_date(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    try:
        date.fromisoformat(text[:10])
        return True
    except ValueError:
        return False


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
    """)
    existing_enq_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(raw_enquiries)").fetchall()
    }
    if "project_ref_code" not in existing_enq_cols:
        conn.execute("ALTER TABLE raw_enquiries ADD COLUMN project_ref_code TEXT")

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
        due_date_raw = str(r.get("contract_due_date", "")).strip()
        age_val, age_ok = _safe_int(r.get("account_age_days_at_t0", ""))
        prior_val, prior_ok = _safe_int(r.get("prior_purchase_count_at_t0", ""))

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
        if not _is_valid_due_date(due_date_raw):
            reasons.append(f"Sai dinh dang contract_due_date ({due_date_raw})")
        if not age_ok:
            reasons.append("Sai kieu so account_age_days_at_t0")
        if not prior_ok:
            reasons.append("Sai kieu so prior_purchase_count_at_t0")

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
            str(r.get("project_ref_code", "")).strip(),
            str(r.get("spec_completeness", "")).strip(),
            str(r.get("is_urgent_replacement", "")).strip(),
            due_date_raw,
            str(r.get("workflow_state", "")).strip(),
            str(r.get("industry_at_t0", "")).strip(),
            str(r.get("enquiry_channel_at_t0", "")).strip(),
            str(r.get("region_at_t0", "")).strip(),
            age_val,
            prior_val,
            str(r.get("estimated_value_band_at_t0", "")).strip(),
            parse_vn_iso(r.get("feature_event_time", "")) or "",
            parse_vn_iso(r.get("feature_available_at", "")) or "",
        ))

    # Kiểm tra & lọc các dòng outcome hợp lệ
    valid_out_rows = []
    quarantine_outcome_rows = []
    for _, row in out_df.iterrows():
        r = row.to_dict()
        reasons = []

        enq_id = str(r.get("enquiry_id", "")).strip()
        event_type = str(r.get("event_type", "")).strip()
        event_time_raw = str(r.get("event_time", "")).strip()
        event_time_iso = parse_vn_iso(event_time_raw)
        label = str(r.get("label", "")).strip()
        maturity_state = str(r.get("maturity_state", "")).strip()
        cohort = str(r.get("cohort", "")).strip()
        obs_days_raw = str(r.get("observation_window_days", "")).strip()
        obs_days_val, obs_days_ok = _safe_int(obs_days_raw)

        if not enq_id:
            reasons.append("Thieu enquiry_id")
        if not event_type:
            reasons.append("Thieu event_type")
        if not event_time_raw or event_time_iso is None:
            reasons.append("Thieu hoac sai dinh dang event_time")
        if not label:
            reasons.append("Thieu label")
        elif label not in VALID_OUTCOME_LABELS:
            reasons.append(f"Sai danh muc label ({label})")
        if not cohort:
            reasons.append("Thieu cohort")
        if maturity_state and maturity_state not in VALID_MATURITY_STATES:
            reasons.append(f"Sai maturity_state ({maturity_state})")
        if not obs_days_ok:
            reasons.append(f"Sai kieu so observation_window_days ({obs_days_raw})")
        elif obs_days_val is not None and obs_days_val < 0:
            reasons.append(f"observation_window_days am ({obs_days_val})")

        if not reasons and enq_id in bad_ids:
            reasons.append("Enquiry tuong ung bi loi schema (quarantined)")

        if reasons:
            r["quarantine_reason"] = "; ".join(reasons)
            quarantine_outcome_rows.append(r)
            continue

        valid_out_rows.append((
            enq_id,
            event_type,
            event_time_iso,
            parse_vn_iso(r.get("recorded_at", "")) or str(r.get("recorded_at", "")).strip(),
            str(r.get("qualification_evidence", "")).strip(),
            str(r.get("business_need_confirmed", "")).strip(),
            str(r.get("product_fit_confirmed", "")).strip(),
            str(r.get("agreed_next_step_confirmed", "")).strip(),
            str(r.get("observer_coverage", "")).strip(),
            obs_days_val,
            maturity_state,
            label,
            str(r.get("correction_status", "")).strip(),
            cohort,
        ))

    # Lưu vào SQLite dùng UPSERT theo enquiry_id
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        conn.executemany(
            """
            INSERT INTO raw_enquiries (
                enquiry_id, account_key, cohort, source, t0_time, extraction_time,
                product_family, project_ref_code, spec_completeness, is_urgent_replacement,
                contract_due_date, workflow_state, industry_at_t0,
                enquiry_channel_at_t0, region_at_t0, account_age_days_at_t0,
                prior_purchase_count_at_t0, estimated_value_band_at_t0,
                feature_event_time, feature_available_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(enquiry_id) DO UPDATE SET
                account_key = excluded.account_key,
                cohort = excluded.cohort,
                source = excluded.source,
                t0_time = excluded.t0_time,
                extraction_time = excluded.extraction_time,
                product_family = excluded.product_family,
                project_ref_code = excluded.project_ref_code,
                spec_completeness = excluded.spec_completeness,
                is_urgent_replacement = excluded.is_urgent_replacement,
                contract_due_date = excluded.contract_due_date,
                workflow_state = excluded.workflow_state,
                industry_at_t0 = excluded.industry_at_t0,
                enquiry_channel_at_t0 = excluded.enquiry_channel_at_t0,
                region_at_t0 = excluded.region_at_t0,
                account_age_days_at_t0 = excluded.account_age_days_at_t0,
                prior_purchase_count_at_t0 = excluded.prior_purchase_count_at_t0,
                estimated_value_band_at_t0 = excluded.estimated_value_band_at_t0,
                feature_event_time = excluded.feature_event_time,
                feature_available_at = excluded.feature_available_at
            WHERE excluded.extraction_time >= raw_enquiries.extraction_time
            """,
            valid_enq_rows,
        )
        conn.executemany(
            """
            INSERT INTO raw_outcomes (
                enquiry_id, event_type, event_time, recorded_at,
                qualification_evidence, business_need_confirmed,
                product_fit_confirmed, agreed_next_step_confirmed,
                observer_coverage, observation_window_days, maturity_state,
                label, correction_status, cohort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(enquiry_id) DO UPDATE SET
                event_type = excluded.event_type,
                event_time = excluded.event_time,
                recorded_at = excluded.recorded_at,
                qualification_evidence = excluded.qualification_evidence,
                business_need_confirmed = excluded.business_need_confirmed,
                product_fit_confirmed = excluded.product_fit_confirmed,
                agreed_next_step_confirmed = excluded.agreed_next_step_confirmed,
                observer_coverage = excluded.observer_coverage,
                observation_window_days = excluded.observation_window_days,
                maturity_state = excluded.maturity_state,
                label = excluded.label,
                correction_status = excluded.correction_status,
                cohort = excluded.cohort
            WHERE COALESCE(NULLIF(excluded.recorded_at, ''), excluded.event_time, '')
               >= COALESCE(NULLIF(raw_outcomes.recorded_at, ''), raw_outcomes.event_time, '')
            """,
            valid_out_rows,
        )

        # SN-BUG-03: Nếu enquiry_id đang hoạt động bị lỗi schema ở lần nạp mới nhất -> tạm ẩn/xóa khỏi DB để không phục vụ sai
        if bad_ids:
            placeholders = ",".join("?" for _ in bad_ids)
            bad_tuple = tuple(bad_ids)
            conn.execute(f"DELETE FROM raw_enquiries WHERE enquiry_id IN ({placeholders})", bad_tuple)
            has_clean = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='clean_enquiries'"
            ).fetchone()
            if has_clean:
                conn.execute(f"DELETE FROM clean_enquiries WHERE enquiry_id IN ({placeholders})", bad_tuple)

        conn.commit()

        db_enq_count = conn.execute("SELECT COUNT(*) FROM raw_enquiries").fetchone()[0]
        db_out_count = conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0]

    # Chỉ ghi file quarantine sau khi commit SQLite thành công
    QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if quarantine_rows:
        pd.DataFrame(quarantine_rows).to_csv(QUARANTINE_PATH, index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=list(enq_df.columns) + ["quarantine_reason"]).to_csv(
            QUARANTINE_PATH, index=False, encoding="utf-8"
        )

    QUARANTINE_OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if quarantine_outcome_rows:
        pd.DataFrame(quarantine_outcome_rows).to_csv(QUARANTINE_OUTCOMES_PATH, index=False, encoding="utf-8")
    else:
        pd.DataFrame(columns=list(out_df.columns) + ["quarantine_reason"]).to_csv(
            QUARANTINE_OUTCOMES_PATH, index=False, encoding="utf-8"
        )

    print("=" * 60)
    print("US-06 | KET QUA KIEM TRA & NAP DU LIEU CSV")
    print("=" * 60)
    print(f"Tong so dong doc (enquiries_snapshot.csv) : {len(enq_df)}")
    print(f"So dong hop le da kiem tra                 : {len(valid_enq_rows)}")
    print(f"So dong loi dua vao Quarantine             : {len(quarantine_rows)}")
    print("-" * 60)
    print(f"Tong so dong trong DB (raw_enquiries)      : {db_enq_count}")
    print(f"Tong so dong trong DB (raw_outcomes)       : {db_out_count}")
    print(f"So dong loi Outcome dua vao Quarantine     : {len(quarantine_outcome_rows)}")
    print(f"File loi Enquiries da ghi tai              : {QUARANTINE_PATH}")
    print(f"File loi Outcomes da ghi tai               : {QUARANTINE_OUTCOMES_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    validate_and_load()