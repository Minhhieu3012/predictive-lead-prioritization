import os
import pandas as pd

# ============================================================
# PLPT-11 / US-03
# Audit script cho dữ liệu chuẩn SongNhat AI-02 trong data/raw/
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENQUIRY_FILE = os.path.join(BASE_DIR, "data", "raw", "enquiries_snapshot.csv")
OUTCOME_FILE = os.path.join(BASE_DIR, "data", "raw", "outcome_events.csv")

MIN_TRAIN = 1000
MIN_TEST = 200
MIN_POSITIVE = 100
MIN_NEGATIVE = 100
MIN_TEST_POSITIVE = 40
MIN_TEST_NEGATIVE = 40
OBSERVATION_DAYS = 60

VALID_PRODUCT_FAMILIES = {"Motor_DongCo", "Drive_BienTan", "Transmission_HopSo"}

ENQUIRY_REQUIRED = [
    "enquiry_id", "account_key", "cohort", "source", "t0_time",
    "extraction_time", "product_family", "spec_completeness",
    "is_urgent_replacement", "contract_due_date", "workflow_state",
    "industry_at_t0", "enquiry_channel_at_t0", "region_at_t0",
    "account_age_days_at_t0", "prior_purchase_count_at_t0",
    "estimated_value_band_at_t0", "feature_event_time",
    "feature_available_at", "contact_phone_DO_NOT_USE",
    "latest_quote_value_post_t0_DO_NOT_USE",
    "latest_quote_available_at_DO_NOT_USE",
]

OUTCOME_REQUIRED = [
    "enquiry_id", "event_type", "event_time", "recorded_at",
    "qualification_evidence", "business_need_confirmed",
    "product_fit_confirmed", "agreed_next_step_confirmed",
    "observer_coverage", "observation_window_days",
    "maturity_state", "label", "correction_status", "cohort",
]

checks = []


def result(name, passed, detail):
    status = "PASS" if passed else "FAIL"
    checks.append((name, status, detail))
    print(f"[{status}] {name}")
    print(f"       {detail}")


def warning(name, detail):
    checks.append((name, "WARNING", detail))
    print(f"[WARNING] {name}")
    print(f"          {detail}")


def main():
    print("=" * 76)
    print("PLPT-11 | US-03 | SONGNHAT AI-02 DATA AUDIT")
    print("=" * 76)

    # --------------------------------------------------------
    # STEP 0 - READ FILES AND VALIDATE SCHEMA
    # --------------------------------------------------------
    print("\n[STEP 0] FILES AND SCHEMA")

    for path in [ENQUIRY_FILE, OUTCOME_FILE]:
        if not os.path.isfile(path):
            print(f"[FATAL] Không tìm thấy file: {path}")
            return

    try:
        enq = pd.read_csv(ENQUIRY_FILE, dtype=str)
        out = pd.read_csv(OUTCOME_FILE, dtype=str)
    except Exception as exc:
        print(f"[FATAL] Không đọc được CSV: {exc}")
        return

    print(f"Enquiry Snapshot: {len(enq)} rows, {len(enq.columns)} columns")
    print(f"Outcome Events  : {len(out)} rows, {len(out.columns)} columns")

    missing_enq = sorted(set(ENQUIRY_REQUIRED) - set(enq.columns))
    missing_out = sorted(set(OUTCOME_REQUIRED) - set(out.columns))

    if missing_enq or missing_out:
        result(
            "Schema",
            False,
            f"Thiếu cột Enquiry={missing_enq}; Outcome={missing_out}",
        )
        print("\nDừng kiểm tra vì schema không đúng.")
        return

    result(
        "Schema",
        True,
        f"Đủ {len(ENQUIRY_REQUIRED)} cột Enquiry Snapshot và {len(OUTCOME_REQUIRED)} cột Outcome Events.",
    )

    for df in (enq, out):
        for col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    # Chuyển đổi ngày giờ an toàn (errors="coerce" để không bị crash khi gặp dòng lỗi)
    enq["t0_time"] = pd.to_datetime(enq["t0_time"], errors="coerce", utc=False)
    enq["extraction_time"] = pd.to_datetime(enq["extraction_time"], errors="coerce", utc=False)
    enq["feature_event_time"] = pd.to_datetime(enq["feature_event_time"], errors="coerce", utc=False)
    enq["feature_available_at"] = pd.to_datetime(enq["feature_available_at"], errors="coerce", utc=False)

    out["event_time"] = pd.to_datetime(out["event_time"], errors="coerce", utc=False)
    out["recorded_at"] = pd.to_datetime(out["recorded_at"], errors="coerce", utc=False)
    out["observation_window_days"] = pd.to_numeric(out["observation_window_days"], errors="coerce")

    # --------------------------------------------------------
    # STEP 0.5 - QUARANTINE CHECK (BỎ QUA DÒNG LỖI, KHÔNG CRASH)
    # --------------------------------------------------------
    print("\n[STEP 0.5] QUARANTINE ERROR ROWS CHECK")

    bad_mask = (
        enq["enquiry_id"].isna()
        | (enq["enquiry_id"] == "")
        | enq["account_key"].isna()
        | (enq["account_key"] == "")
        | enq["t0_time"].isna()
        | ~enq["product_family"].isin(VALID_PRODUCT_FAMILIES)
    )
    quarantine_count = int(bad_mask.sum())
    bad_ids = set(enq.loc[bad_mask, "enquiry_id"].dropna().tolist())

    print(f"Số dòng lỗi phát hiện (cần đưa vào Quarantine): {quarantine_count}")
    if quarantine_count > 0:
        print(f"Ví dụ mã lỗi: {sorted(list(bad_ids))[:5]}")

    result(
        "Quarantine detection",
        True,
        f"Đã phát hiện và bỏ qua an toàn {quarantine_count} dòng lỗi cần đưa vào Quarantine.",
    )

    # Tách bỏ dòng lỗi để kiểm tra tiếp các dòng hợp lệ
    enq = enq[~bad_mask].copy()
    out = out[~out["enquiry_id"].isin(bad_ids)].copy()

    # --------------------------------------------------------
    # STEP 1 - ID UNIQUENESS AND JOIN
    # --------------------------------------------------------
    print("\n[STEP 1] ID UNIQUENESS AND JOIN")

    enq_duplicate_ids = int(enq["enquiry_id"].duplicated().sum())
    out_duplicate_ids = int(out["enquiry_id"].duplicated().sum())

    ids_unique = enq_duplicate_ids == 0 and out_duplicate_ids == 0
    result(
        "Unique enquiry_id",
        ids_unique,
        "Mỗi enquiry_id chỉ xuất hiện một lần trong từng file."
        if ids_unique else "Có enquiry_id bị trùng.",
    )
    if not ids_unique:
        return

    merged = enq.merge(
        out,
        on="enquiry_id",
        how="outer",
        suffixes=("_enq", "_out"),
        indicator=True,
        validate="one_to_one",
    )

    unmatched = int((merged["_merge"] != "both").sum())
    result(
        "One-to-one join",
        unmatched == 0,
        f"Ghép được {len(merged) - unmatched}/{len(merged)} hồ sơ hợp lệ; không ghép được: {unmatched}.",
    )
    if unmatched:
        return

    merged = merged[merged["_merge"] == "both"].copy()

    dup_accounts = int(enq["account_key"].duplicated(keep=False).sum())
    print(f"Số dòng có account_key lặp lại (cần gộp trùng khi ingest): {dup_accounts}")

    # --------------------------------------------------------
    # STEP 2 - COHORT CONSISTENCY
    # --------------------------------------------------------
    print("\n[STEP 2] COHORT CONSISTENCY")

    cohort_mismatch = int((merged["cohort_enq"] != merged["cohort_out"]).sum())
    result(
        "Cohort consistency",
        cohort_mismatch == 0,
        f"Số hồ sơ có cohort khác nhau giữa hai file: {cohort_mismatch}.",
    )
    print(f"Enquiry cohort counts: {enq['cohort'].value_counts(dropna=False).to_dict()}")

    # --------------------------------------------------------
    # STEP 3 - AC1 VOLUME FLOOR
    # --------------------------------------------------------
    print("\n[AC1] HISTORICAL TRAINING VOLUME FLOOR")

    train = merged[merged["cohort_enq"] == "TRAINING_CANDIDATE"].copy()
    train_mature = train[train["maturity_state"] == "MATURE"].copy()

    train_positive = train_mature[train_mature["label"] == "POSITIVE"]
    train_negative = train_mature[train_mature["label"] == "NEGATIVE"]

    train_count = train_mature["enquiry_id"].nunique()
    train_p = train_positive["enquiry_id"].nunique()
    train_n = train_negative["enquiry_id"].nunique()

    print(f"Training mature: {train_count} / {MIN_TRAIN}")
    print(f"Training Positive: {train_p} / {MIN_POSITIVE}")
    print(f"Training Negative: {train_n} / {MIN_NEGATIVE}")

    positive_evidence_ok = (
        train_positive["business_need_confirmed"].eq("Yes")
        & train_positive["product_fit_confirmed"].eq("Yes")
        & train_positive["agreed_next_step_confirmed"].eq("Yes")
    ).all()

    negative_coverage_ok = (
        train_negative["observer_coverage"].eq("Complete")
        & train_negative["observation_window_days"].eq(OBSERVATION_DAYS)
    ).all()

    ac1_ok = (
        train_count >= MIN_TRAIN
        and train_p >= MIN_POSITIVE
        and train_n >= MIN_NEGATIVE
        and positive_evidence_ok
        and negative_coverage_ok
    )

    result(
        "AC1 Volume Floor",
        ac1_ok,
        "Training cohort đạt ngưỡng số lượng và kiểm tra bằng chứng nhãn."
        if ac1_ok else "Training cohort chưa đạt một hoặc nhiều điều kiện AC1.",
    )

    # --------------------------------------------------------
    # STEP 4 - AC2 SEALED LATER TEST COHORT
    # --------------------------------------------------------
    print("\n[AC2] SEALED LATER TEST COHORT")

    test = merged[merged["cohort_enq"] == "SEALED_LATER_TEST"].copy()
    test_mature = test[test["maturity_state"] == "MATURE"].copy()

    test_count = test_mature["enquiry_id"].nunique()
    test_p = int((test_mature["label"] == "POSITIVE").sum())
    test_n = int((test_mature["label"] == "NEGATIVE").sum())

    print(f"Sealed test mature: {test_count} / {MIN_TEST}")
    print(f"Test Positive: {test_p} / {MIN_TEST_POSITIVE}")
    print(f"Test Negative: {test_n} / {MIN_TEST_NEGATIVE}")

    train_t0_max = train_mature["t0_time"].max()
    test_t0_min = test_mature["t0_time"].min()

    temporal_ok = (
        pd.notna(train_t0_max)
        and pd.notna(test_t0_min)
        and train_t0_max < test_t0_min
    )

    ac2_ok = (
        test_count >= MIN_TEST
        and test_p >= MIN_TEST_POSITIVE
        and test_n >= MIN_TEST_NEGATIVE
        and len(train_mature) >= MIN_TRAIN
        and temporal_ok
    )

    result(
        "AC2 Independent Later Test Cohort",
        ac2_ok,
        "Cohort test có đủ số lượng P/N và nằm sau giai đoạn training."
        if ac2_ok else "Test cohort chưa đạt một hoặc nhiều điều kiện AC2.",
    )

    # --------------------------------------------------------
    # STEP 5 - AC3 FEATURE AVAILABILITY / LEAKAGE
    # --------------------------------------------------------
    print("\n[AC3] FEATURE AVAILABILITY AUDIT")

    t0 = enq["t0_time"]
    event_time = enq["feature_event_time"]
    available_at = enq["feature_available_at"]

    event_invalid = int((t0.isna() | event_time.isna() | (event_time > t0)).sum())
    available_invalid = int((t0.isna() | available_at.isna() | (available_at > t0)).sum())

    result(
        "Feature event time",
        event_invalid == 0,
        "Tất cả feature_event_time có trước hoặc đúng t0_time."
        if event_invalid == 0 else f"Có {event_invalid} dòng feature_event_time sau t0_time.",
    )

    result(
        "Feature availability time",
        available_invalid == 0,
        "Tất cả feature_available_at có trước hoặc đúng t0_time."
        if available_invalid == 0 else f"Có {available_invalid} dòng feature_available_at sau t0_time.",
    )

    forbidden_columns = [col for col in enq.columns if col.endswith("_DO_NOT_USE")]
    print("Các cột bị cấm đưa vào biến tính điểm (_DO_NOT_USE):")
    for col in forbidden_columns:
        print(f"  - {col}")

    # --------------------------------------------------------
    # STEP 6 - AC4 MISSINGNESS AND LABEL COVERAGE
    # --------------------------------------------------------
    print("\n[AC4] MISSINGNESS AND LABEL COVERAGE")

    eligible = merged[merged["maturity_state"] == "MATURE"].copy()
    known_labels = eligible[eligible["label"].isin(["POSITIVE", "NEGATIVE"])]
    unknown_open = merged[merged["label"] == "Unknown"]

    coverage = len(known_labels) / len(eligible) * 100 if len(eligible) else 0
    print(f"Hồ sơ đủ 60 ngày (MATURE): {len(eligible)} | Label coverage: {coverage:.2f}%")
    print(f"Hồ sơ mới chưa đủ 60 ngày (Unknown): {len(unknown_open)}")

    result(
        "AC4 Label coverage",
        coverage >= 90,
        f"Label coverage {coverage:.2f}% (ngưỡng >= 90%).",
    )

    # --------------------------------------------------------
    # STEP 7 - FINAL SUMMARY
    # --------------------------------------------------------
    print("\n" + "=" * 76)
    print("FINAL SUMMARY")
    print("=" * 76)
    pass_count = sum(1 for _, status, _ in checks if status == "PASS")
    fail_count = sum(1 for _, status, _ in checks if status == "FAIL")
    warning_count = sum(1 for _, status, _ in checks if status == "WARNING")
    print(f"PASS       : {pass_count}")
    print(f"FAIL       : {fail_count}")
    print(f"WARNING    : {warning_count}")
    print(f"QUARANTINE : {quarantine_count} dòng lỗi đã được tách riêng")
    print("=" * 76)


if __name__ == "__main__":
    main()