import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

VN_TZ = timezone(timedelta(hours=7))
RANDOM_SEED = 42

PRODUCT_FAMILIES = ["Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"]
CHANNELS = ["Website", "Email", "Phone", "Trade Fair", "Referral"]
SOURCES = ["Website", "Direct Sales", "Referral", "Trade Fair"]
INDUSTRIES = ["Manufacturing", "Food Processing", "Construction", "Logistics", "Packaging"]
REGIONS = ["North", "Central", "South"]
VALUE_BANDS = ["Under_50M", "50M_200M", "200M_500M", "Over_500M"]
WORKFLOW_STATES = ["New", "Contacted", "Under_Review", "Quoted", "Closed"]
SPEC_LEVELS = ["Yes", "No"]


def to_vn_iso(dt: datetime) -> str:
    return dt.astimezone(VN_TZ).replace(microsecond=0).isoformat()


def build_record(
    idx: int,
    prefix: str,
    cohort: str,
    t0_dt: datetime,
    extraction_dt: datetime,
    account_key: str,
    product_family: str,
    channel: str,
    is_positive: bool | None,
    is_bad_row: bool = False,
    bad_type: str = "",
) -> tuple[dict, dict]:
    enquiry_id = f"{prefix}-{idx:04d}"

    feat_event_dt = t0_dt - timedelta(hours=random.randint(2, 72))
    feat_avail_dt = t0_dt - timedelta(minutes=random.randint(10, 600))
    due_dt = t0_dt + timedelta(days=random.randint(7, 45))

    if is_bad_row:
        t0_str = "" if bad_type == "missing_t0" else "2025/99/99-SAI-DINH-DANG"
    else:
        t0_str = to_vn_iso(t0_dt)

    enq_row = {
        "enquiry_id": enquiry_id,
        "account_key": account_key,
        "cohort": cohort,
        "source": random.choice(SOURCES),
        "t0_time": t0_str,
        "extraction_time": to_vn_iso(extraction_dt),
        "product_family": product_family,
        "spec_completeness": random.choice(SPEC_LEVELS),
        "is_urgent_replacement": random.choice(["Urgent", "Normal"]),
        "contract_due_date": due_dt.strftime("%Y-%m-%d"),
        "workflow_state": random.choice(WORKFLOW_STATES),
        "industry_at_t0": random.choice(INDUSTRIES),
        "enquiry_channel_at_t0": channel,
        "region_at_t0": random.choice(REGIONS),
        "account_age_days_at_t0": random.choice([14, 30, 90, 180, 365, 730]),
        "prior_purchase_count_at_t0": random.choice([0, 1, 2, 3, 5]),
        "estimated_value_band_at_t0": random.choice(VALUE_BANDS),
        "feature_event_time": to_vn_iso(feat_event_dt),
        "feature_available_at": to_vn_iso(feat_avail_dt),
        "contact_phone_DO_NOT_USE": f"090{random.randint(1000000, 9999999)}",
        "latest_quote_value_post_t0_DO_NOT_USE": round(random.uniform(20, 500), 1) if is_positive else "",
        "latest_quote_available_at_DO_NOT_USE": to_vn_iso(t0_dt + timedelta(days=10)) if is_positive else "",
    }

    if is_positive is None:
        out_row = {
            "enquiry_id": enquiry_id,
            "event_type": "PENDING_OBSERVATION",
            "event_time": to_vn_iso(t0_dt),
            "recorded_at": to_vn_iso(extraction_dt),
            "qualification_evidence": "Within 60-day observation window",
            "business_need_confirmed": "Unknown",
            "product_fit_confirmed": "Unknown",
            "agreed_next_step_confirmed": "Unknown",
            "observer_coverage": "In_Progress",
            "observation_window_days": 1,
            "maturity_state": "IMMATURE",
            "label": "Unknown",
            "correction_status": "No correction",
            "cohort": cohort,
        }
    elif is_positive:
        event_dt = t0_dt + timedelta(days=random.randint(5, 50))
        out_row = {
            "enquiry_id": enquiry_id,
            "event_type": "QUALIFICATION_CONFIRMED",
            "event_time": to_vn_iso(event_dt),
            "recorded_at": to_vn_iso(event_dt),
            "qualification_evidence": "Business need + product fit + agreed next step documented",
            "business_need_confirmed": "Yes",
            "product_fit_confirmed": "Yes",
            "agreed_next_step_confirmed": "Yes",
            "observer_coverage": "Complete",
            "observation_window_days": 60,
            "maturity_state": "MATURE",
            "label": "POSITIVE",
            "correction_status": "No correction",
            "cohort": cohort,
        }
    else:
        event_dt = t0_dt + timedelta(days=60)
        out_row = {
            "enquiry_id": enquiry_id,
            "event_type": "60_DAY_MATURITY_NO_QUALIFICATION",
            "event_time": to_vn_iso(event_dt),
            "recorded_at": to_vn_iso(event_dt),
            "qualification_evidence": "No qualifying event recorded during full 60-day window",
            "business_need_confirmed": "No",
            "product_fit_confirmed": "No",
            "agreed_next_step_confirmed": "No",
            "observer_coverage": "Complete",
            "observation_window_days": 60,
            "maturity_state": "MATURE",
            "label": "NEGATIVE",
            "correction_status": "No correction",
            "cohort": cohort,
        }

    return enq_row, out_row


def main():
    random.seed(RANDOM_SEED)
    now_vn = datetime.now(VN_TZ).replace(microsecond=0)

    enq_rows = []
    out_rows = []

    # 1. Tạo 1.000 dòng TRAINING_CANDIDATE (có 20 cặp trùng account_key qua 2 kênh)
    train_start = datetime(2025, 1, 1, 8, 0, tzinfo=VN_TZ)
    for i in range(1, 1001):
        if i <= 40:
            pair_idx = (i - 1) // 2 + 1
            acct = f"ACCT-DUP-{pair_idx:03d}"
            fam = PRODUCT_FAMILIES[pair_idx % len(PRODUCT_FAMILIES)]
            ch = "Website" if i % 2 == 1 else "Email"
        else:
            acct = f"ACCT-{random.randint(1, 800):04d}"
            fam = random.choice(PRODUCT_FAMILIES)
            ch = random.choice(CHANNELS)

        t0_dt = train_start + timedelta(hours=random.randint(0, 150 * 24))
        is_pos = i <= 150
        e, o = build_record(i, "SN", "TRAINING_CANDIDATE", t0_dt, now_vn, acct, fam, ch, is_pos)
        enq_rows.append(e)
        out_rows.append(o)

    # 2. Tạo 200 dòng SEALED_LATER_TEST
    test_start = datetime(2025, 9, 1, 8, 0, tzinfo=VN_TZ)
    for i in range(1001, 1201):
        acct = f"ACCT-{random.randint(1, 800):04d}"
        fam = random.choice(PRODUCT_FAMILIES)
        ch = random.choice(CHANNELS)
        t0_dt = test_start + timedelta(hours=random.randint(0, 150 * 24))
        is_pos = (i - 1000) <= 50
        e, o = build_record(i, "SN", "SEALED_LATER_TEST", t0_dt, now_vn, acct, fam, ch, is_pos)
        enq_rows.append(e)
        out_rows.append(o)

    # 3. Tạo 50 dòng đang mở trong 24h gần nhất (chưa đủ 60 ngày -> Unknown)
    for i in range(1201, 1251):
        acct = f"ACCT-{random.randint(1, 800):04d}"
        fam = random.choice(PRODUCT_FAMILIES)
        ch = random.choice(CHANNELS)
        t0_dt = now_vn - timedelta(hours=random.randint(1, 23))
        e, o = build_record(i, "SN", "OPEN_24H", t0_dt, now_vn, acct, fam, ch, None)
        enq_rows.append(e)
        out_rows.append(o)

    # 4. Cài sẵn 10 dòng lỗi (5 thiếu t0_time, 5 sai định dạng ngày) để test Quarantine
    for j in range(1, 11):
        acct = f"ACCT-ERR-{j:03d}"
        fam = random.choice(PRODUCT_FAMILIES)
        ch = random.choice(CHANNELS)
        bad_type = "missing_t0" if j <= 5 else "invalid_date"
        e, o = build_record(
            j, "ERR", "QUARANTINE_TEST", now_vn, now_vn, acct, fam, ch, None,
            is_bad_row=True, bad_type=bad_type
        )
        enq_rows.append(e)
        out_rows.append(o)

    raw_dir = Path("data/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)

    enq_path = raw_dir / "enquiries_snapshot.csv"
    out_path = raw_dir / "outcome_events.csv"

    pd.DataFrame(enq_rows).to_csv(enq_path, index=False, encoding="utf-8")
    pd.DataFrame(out_rows).to_csv(out_path, index=False, encoding="utf-8")

    print(f"Da tao {enq_path} ({len(enq_rows)} dong)")
    print(f"Da tao {out_path} ({len(out_rows)} dong)")


if __name__ == "__main__":
    main()