import os
import pandas as pd

# ============================================================
# PLPT-11 / US-03
# Audit script cho đúng bộ dữ liệu SYNTHETIC ban đầu.
#
# Files nằm cùng thư mục với code03.py:
#   Enquiry_Snapshot_SYNTHETIC.csv
#   Outcome_Events_SYNTHETIC.csv
#
# Cài pandas nếu cần:
#   python -m pip install pandas
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENQUIRY_FILE = os.path.join(BASE_DIR, "Enquiry_Snapshot_SYNTHETIC.csv")
OUTCOME_FILE = os.path.join(BASE_DIR, "Outcome_Events_SYNTHETIC.csv")

# Ngưỡng AC theo bộ tiêu chí đang dùng để thực hành
MIN_MATURE_TOTAL = 1000
MIN_TRAIN = 1000
MIN_TEST = 200
MIN_POSITIVE = 100
MIN_NEGATIVE = 100
MIN_TEST_POSITIVE = 40
MIN_TEST_NEGATIVE = 40
OBSERVATION_DAYS = 60

ENQUIRY_REQUIRED = [
    "enquiry_key", "account_key", "cohort", "source", "t0",
    "industry_at_t0", "product_category_at_t0",
    "enquiry_channel_at_t0", "region_at_t0",
    "account_age_days_at_t0", "prior_purchase_count_at_t0",
    "request_completeness_at_t0", "urgency_flag_at_t0",
    "estimated_value_band_at_t0", "feature_event_time",
    "feature_available_at", "source_version",
    "current_workflow_state_export_only",
    "latest_quote_value_post_t0_DO_NOT_USE",
    "latest_quote_available_at_DO_NOT_USE",
]

OUTCOME_REQUIRED = [
    "enquiry_key", "event_type", "event_time", "recorded_at",
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
    print("PLPT-11 | US-03 | SYNTHETIC DATA AUDIT")
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
        enq = pd.read_csv(ENQUIRY_FILE)
        out = pd.read_csv(OUTCOME_FILE)
    except Exception as exc:
        print(f"[FATAL] Không đọc được CSV: {exc}")
        return

    print(f"Enquiry Snapshot: {len(enq)} rows, {len(enq.columns)} columns")
    print(f"Outcome Events  : {len(out)} rows, {len(out.columns)} columns")

    missing_enq = sorted(set(ENQUIRY_REQUIRED) - set(enq.columns))
    missing_out = sorted(set(OUTCOME_REQUIRED) - set(out.columns))

    schema_ok = not missing_enq and not missing_out
    if not schema_ok:
        result(
            "Schema",
            False,
            f"Thiếu cột Enquiry={missing_enq}; Outcome={missing_out}"
        )
        print("\nDừng kiểm tra vì schema không đúng.")
        return

    result(
        "Schema",
        True,
        "Đủ 20 cột Enquiry Snapshot và 14 cột Outcome Events theo bộ SYNTHETIC."
    )

    # Chuẩn hóa chuỗi và ngày giờ
    for df in (enq, out):
        for col in df.select_dtypes(include=["object"]).columns:
            df[col] = df[col].astype("string").str.strip()

    for col in ["t0", "feature_event_time", "feature_available_at"]:
        enq[col] = pd.to_datetime(enq[col], errors="coerce")

    for col in ["event_time", "recorded_at"]:
        out[col] = pd.to_datetime(out[col], errors="coerce")

    out["observation_window_days"] = pd.to_numeric(
        out["observation_window_days"], errors="coerce"
    )

    # --------------------------------------------------------
    # STEP 1 - ID UNIQUENESS AND JOIN
    # --------------------------------------------------------
    print("\n[STEP 1] ID UNIQUENESS AND JOIN")

    enq_duplicate_ids = int(enq["enquiry_key"].duplicated().sum())
    out_duplicate_ids = int(out["enquiry_key"].duplicated().sum())

    print(f"Duplicate enquiry_key in Enquiry: {enq_duplicate_ids}")
    print(f"Duplicate enquiry_key in Outcome: {out_duplicate_ids}")

    ids_unique = enq_duplicate_ids == 0 and out_duplicate_ids == 0
    result(
        "Unique enquiry_key",
        ids_unique,
        "Mỗi enquiry_key chỉ xuất hiện một lần trong từng file."
        if ids_unique else "Có enquiry_key bị trùng."
    )
    if not ids_unique:
        print("\nDừng kiểm tra vì khóa ghép không duy nhất.")
        return

    merged = enq.merge(
        out,
        on="enquiry_key",
        how="outer",
        suffixes=("_enq", "_out"),
        indicator=True,
        validate="one_to_one",
    )

    unmatched = int((merged["_merge"] != "both").sum())
    result(
        "One-to-one join",
        unmatched == 0,
        f"Ghép được {len(merged) - unmatched}/{len(merged)} hồ sơ; "
        f"không ghép được: {unmatched}."
    )
    if unmatched:
        print("\nDừng kiểm tra vì hai file không khớp đầy đủ.")
        return

    merged = merged[merged["_merge"] == "both"].copy()

    # --------------------------------------------------------
    # STEP 2 - COHORT CONSISTENCY
    # --------------------------------------------------------
    print("\n[STEP 2] COHORT CONSISTENCY")

    cohort_mismatch = int(
        (merged["cohort_enq"] != merged["cohort_out"]).sum()
    )
    result(
        "Cohort consistency",
        cohort_mismatch == 0,
        f"Số hồ sơ có cohort khác nhau giữa hai file: {cohort_mismatch}."
    )

    enq_cohort_counts = enq["cohort"].value_counts(dropna=False).to_dict()
    out_cohort_counts = out["cohort"].value_counts(dropna=False).to_dict()

    print(f"Enquiry cohort counts: {enq_cohort_counts}")
    print(f"Outcome cohort counts: {out_cohort_counts}")

    # --------------------------------------------------------
    # STEP 3 - AC1 VOLUME FLOOR
    # TRAINING_CANDIDATE is the training cohort, not the test cohort.
    # --------------------------------------------------------
    print("\n[AC1] HISTORICAL TRAINING VOLUME FLOOR")

    train = merged[merged["cohort_enq"] == "TRAINING_CANDIDATE"].copy()
    train_mature = train[train["maturity_state"] == "MATURE"].copy()

    train_positive = train_mature[train_mature["label"] == "POSITIVE"]
    train_negative = train_mature[train_mature["label"] == "NEGATIVE"]

    train_count = train_mature["enquiry_key"].nunique()
    train_p = train_positive["enquiry_key"].nunique()
    train_n = train_negative["enquiry_key"].nunique()

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

    print(f"Positive qualification evidence complete: {positive_evidence_ok}")
    print(f"Negative observation complete for 60 days: {negative_coverage_ok}")

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
        if ac1_ok else "Training cohort chưa đạt một hoặc nhiều điều kiện AC1."
    )

    # --------------------------------------------------------
    # STEP 4 - AC2 SEALED LATER TEST COHORT
    # Use the cohort explicitly marked SEALED_LATER_TEST.
    # Do not manufacture a split by taking the last 200 rows.
    # --------------------------------------------------------
    print("\n[AC2] SEALED LATER TEST COHORT")

    test = merged[merged["cohort_enq"] == "SEALED_LATER_TEST"].copy()
    test_mature = test[test["maturity_state"] == "MATURE"].copy()

    test_count = test_mature["enquiry_key"].nunique()
    test_p = int((test_mature["label"] == "POSITIVE").sum())
    test_n = int((test_mature["label"] == "NEGATIVE").sum())

    print(f"Sealed test mature: {test_count} / {MIN_TEST}")
    print(f"Test Positive: {test_p} / {MIN_TEST_POSITIVE}")
    print(f"Test Negative: {test_n} / {MIN_TEST_NEGATIVE}")

    train_t0_max = train_mature["t0"].max()
    test_t0_min = test_mature["t0"].min()

    temporal_ok = (
        pd.notna(train_t0_max)
        and pd.notna(test_t0_min)
        and train_t0_max < test_t0_min
    )

    print(f"Training latest t0: {train_t0_max}")
    print(f"Test earliest t0   : {test_t0_min}")
    print(f"Training period ends before test begins: {temporal_ok}")

    test_positive_rate = (
        test_p / test_count * 100 if test_count else 0
    )
    print(f"Test Positive prevalence: {test_positive_rate:.2f}%")

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
        if ac2_ok else "Test cohort chưa đạt một hoặc nhiều điều kiện AC2."
    )

    warning(
        "AC2 Sealing and natural prevalence",
        "File có nhãn SEALED_LATER_TEST và tỷ lệ P/N để kiểm tra. "
        "Script không thể chứng minh nhóm đã khóa quyền truy cập test "
        "trước khi huấn luyện, hoặc tỷ lệ này là prevalence tự nhiên từ CRM."
    )

    # --------------------------------------------------------
    # STEP 5 - AC3 FEATURE AVAILABILITY / LEAKAGE
    # --------------------------------------------------------
    print("\n[AC3] FEATURE AVAILABILITY AUDIT")

    t0 = enq["t0"]
    event_time = enq["feature_event_time"]
    available_at = enq["feature_available_at"]

    event_invalid = int(
        (t0.isna() | event_time.isna() | (event_time > t0)).sum()
    )
    available_invalid = int(
        (t0.isna() | available_at.isna() | (available_at > t0)).sum()
    )

    print(f"Missing/invalid/after-t0 feature_event_time: {event_invalid}")
    print(f"Missing/invalid/after-t0 feature_available_at: {available_invalid}")

    result(
        "Feature event time",
        event_invalid == 0,
        "Tất cả feature_event_time có trước hoặc đúng T0."
        if event_invalid == 0 else "Có feature event thiếu hoặc sau T0."
    )

    result(
        "Feature availability time",
        available_invalid == 0,
        "Tất cả feature_available_at có trước hoặc đúng T0."
        if available_invalid == 0 else "Có feature chưa sẵn có tại T0."
    )

    # These columns are explicitly not valid prediction inputs.
    forbidden_columns = [
        "current_workflow_state_export_only",
        "latest_quote_value_post_t0_DO_NOT_USE",
        "latest_quote_available_at_DO_NOT_USE",
    ]

    print("Columns explicitly excluded from prediction features:")
    for col in forbidden_columns:
        print(f"  - {col}")

    warning(
        "Post-t0 feature exclusion",
        "Tên cột đánh dấu rõ các trường không được dùng. "
        "CSV không thể chứng minh pipeline/model thực tế đã loại chúng; "
        "cần đối chiếu feature list hoặc code huấn luyện."
    )

    warning(
        "Feature semantic availability",
        "Timestamp đạt điều kiện không tự chứng minh mọi giá trị feature "
        "được tạo đúng từ nguồn dữ liệu lịch sử tại T0. Cần data owner "
        "xác nhận nguồn và cách snapshot được tạo."
    )

    # --------------------------------------------------------
    # STEP 6 - AC4 MISSINGNESS AND LABEL COVERAGE
    # --------------------------------------------------------
    print("\n[AC4] MISSINGNESS AND LABEL COVERAGE")

    eligible = merged[merged["maturity_state"] == "MATURE"].copy()
    known_labels = eligible[eligible["label"].isin(["POSITIVE", "NEGATIVE"])]

    eligible_count = len(eligible)
    known_count = len(known_labels)
    coverage = known_count / eligible_count * 100 if eligible_count else 0

    print(f"Eligible mature records: {eligible_count}")
    print(f"Known Positive/Negative labels: {known_count}")
    print(f"Label coverage: {coverage:.2f}%")

    result(
        "AC4 Label coverage",
        coverage >= 90,
        f"Label coverage {coverage:.2f}% (reference check >= 90%)."
    )

    print("\nMissingness by snapshot field:")
    for col in ENQUIRY_REQUIRED:
        missing = int(enq[col].isna().sum())
        rate = missing / len(enq) * 100 if len(enq) else 0
        print(f"  {col}: {missing}/{len(enq)} ({rate:.2f}%)")

    print("\nMissingness by outcome field:")
    for col in OUTCOME_REQUIRED:
        missing = int(out[col].isna().sum())
        rate = missing / len(out) * 100 if len(out) else 0
        print(f"  {col}: {missing}/{len(out)} ({rate:.2f}%)")

    warning(
        "Critical-field missingness thresholds",
        "Báo cáo missingness theo từng cột; RFP không quy định một "
        "ngưỡng thiếu dữ liệu chung cho mọi field. Data owner cần duyệt "
        "ngưỡng riêng cho các field quan trọng."
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

    print(f"PASS    : {pass_count}")
    print(f"FAIL    : {fail_count}")
    print(f"WARNING : {warning_count}")

    if fail_count:
        print("\nRESULT: NOT READY - Cần xử lý các mục FAIL.")
    elif warning_count:
        print(
            "\nRESULT: AUTOMATED CHECKS PASS WITH WARNINGS - "
            "Chưa đủ căn cứ để tự kết luận Gate GO."
        )
    else:
        print("\nRESULT: ALL AUTOMATED CHECKS PASS.")

    print("\nIMPORTANT:")
    print("- Đây là dữ liệu SYNTHETIC để luyện tập, không phải dữ liệu khách hàng thật.")
    print("- Kết quả PASS trên dữ liệu giả lập không chứng minh dữ liệu thật đạt G0.")
    print("- Cần người phụ trách dữ liệu xác nhận schema, cohort, feature list và test sealing.")
    print("=" * 76)


if __name__ == "__main__":
    main()
