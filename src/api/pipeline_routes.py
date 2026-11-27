"""Sprint 2 Browser Console Routes: Data Pipeline (PLPT-14..17) & DEV-ONLY QA Sandbox."""

import contextlib
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
import pandas as pd

from scripts.dev import generate_synthetic_data
from src.ingest import csv_validator, deduplicator
from src.security.auth import AuthContext, get_env_mode, record_security_audit_log
from src.security.rbac import is_rbac_enforced_on_v1, require_roles
from src.services.queue_service import (
    STALE_HOLD_HOURS,
    STALE_STATUS,
    ensure_operational_tables,
    get_db_path,
    get_quarantine_dir,
    load_enquiries_and_meta,
)

VN_TZ = timezone(timedelta(hours=7))
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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

# Danh sách cố định (allowlist) các lệnh kiểm thử Sprint 2 chỉ chạy khi ENV_MODE=development
ALLOWED_SPRINT2_CHECKS: dict[str, dict[str, Any]] = {
    "plpt14_sync": {
        "label": "PLPT-14: pytest test_pipeline_sync.py",
        "kind": "pytest",
        "targets": ["tests/core_pipeline/test_pipeline_sync.py"],
    },
    "plpt15_dedup": {
        "label": "PLPT-15: pytest test_deduplicator.py + test_cf09_steward_dedup.py",
        "kind": "pytest",
        "targets": [
            "tests/core_pipeline/test_deduplicator.py",
            "tests/control_fixtures/test_cf09_steward_dedup.py",
        ],
    },
    "plpt16_rules": {
        "label": "PLPT-16: pytest test_rules_engine.py",
        "kind": "pytest",
        "targets": ["tests/core_pipeline/test_rules_engine.py"],
    },
    "plpt17_blocking": {
        "label": "PLPT-17: pytest test_plpt17_blocking.py",
        "kind": "pytest",
        "targets": ["tests/core_pipeline/test_plpt17_blocking.py"],
    },
    "sprint2_all": {
        "label": "DoD Sprint 2: Toàn bộ tests/core_pipeline/ + test_cf09_steward_dedup.py",
        "kind": "pytest",
        "targets": [
            "tests/core_pipeline/",
            "tests/control_fixtures/test_cf09_steward_dedup.py",
        ],
    },
    "git_security": {
        "label": "DoD Sprint 2: pre-commit gitleaks + forbid-real-env-files",
        "kind": "pre_commit",
        "hooks": ["gitleaks", "forbid-real-env-files"],
    },
}

router = APIRouter(tags=["Sprint 2 Pipeline & QA Console"])


def _require_dev_mode() -> None:
    """SEC-06: Chặn tuyệt đối các thao tác giả lập/test sandbox ngoài ENV_MODE=development."""
    mode = get_env_mode()
    if mode != "development":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Thao tác QA/Demo Sandbox bị khóa ở ENV_MODE={mode} "
                "(chỉ được phép chạy trong ENV_MODE=development theo SEC-06)."
            ),
        )


def _run_csv_validator_for_active_paths() -> None:
    """Chạy csv_validator đồng bộ với DATABASE_PATH và QUARANTINE_DIR hiện hành."""
    orig = (
        csv_validator.DB_PATH,
        csv_validator.QUARANTINE_PATH,
        csv_validator.QUARANTINE_OUTCOMES_PATH,
    )
    try:
        q_dir = get_quarantine_dir()
        csv_validator.DB_PATH = get_db_path()
        csv_validator.QUARANTINE_PATH = q_dir / "quarantine_rows.csv"
        csv_validator.QUARANTINE_OUTCOMES_PATH = q_dir / "quarantine_outcomes.csv"
        csv_validator.validate_and_load()
    finally:
        (
            csv_validator.DB_PATH,
            csv_validator.QUARANTINE_PATH,
            csv_validator.QUARANTINE_OUTCOMES_PATH,
        ) = orig


def _read_csv_preview(path: Path, limit: int = 15) -> tuple[int, list[dict[str, str]]]:
    if not path.exists():
        return 0, []
    rows: list[dict[str, str]] = []
    total = 0
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if len(rows) < limit:
                rows.append({str(k): str(v or "") for k, v in row.items()})
    return total, rows


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    has_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not has_tbl:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


@router.get("/api/v1/pipeline/status")
def get_pipeline_status(
    _auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    Trả toàn bộ trạng thái bảng SQLite & Quarantine phục vụ kiểm chứng Sprint 2 trực tiếp trên trình duyệt
    (thay thế việc chạy lệnh python -c / SQLite Viewer thủ công).
    """
    db_path = get_db_path()
    q_dir = get_quarantine_dir()
    enq_csv = PROJECT_ROOT / "data" / "raw" / "enquiries_snapshot.csv"
    out_csv = PROJECT_ROOT / "data" / "raw" / "outcome_events.csv"

    enq_csv_rows, _ = _read_csv_preview(enq_csv, limit=0)
    out_csv_rows, _ = _read_csv_preview(out_csv, limit=0)
    q_enq_count, q_enq_preview = _read_csv_preview(q_dir / "quarantine_rows.csv", limit=15)
    q_out_count, _ = _read_csv_preview(q_dir / "quarantine_outcomes.csv", limit=0)

    raw_enq_cols: list[str] = []
    raw_enq_count = 0
    raw_out_count = 0
    clean_enq_count = 0
    merge_count = 0
    steward_count = 0
    blocked_count = 0
    label_counts: dict[str, int] = {}
    merge_preview: list[dict[str, Any]] = []
    steward_preview: list[dict[str, Any]] = []
    blocked_preview: list[dict[str, Any]] = []

    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.row_factory = sqlite3.Row
            has_raw_enq = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_enquiries'"
            ).fetchone()
            if has_raw_enq:
                raw_enq_cols = [
                    str(r["name"])
                    for r in conn.execute("PRAGMA table_info(raw_enquiries)").fetchall()
                ]
                raw_enq_count = int(conn.execute("SELECT COUNT(*) FROM raw_enquiries").fetchone()[0])

            raw_out_count = _count_table(conn, "raw_outcomes")
            if raw_out_count > 0:
                for r in conn.execute(
                    "SELECT COALESCE(label, 'Unknown') AS lbl, COUNT(*) AS cnt FROM raw_outcomes GROUP BY lbl"
                ).fetchall():
                    label_counts[str(r["lbl"])] = int(r["cnt"])

            clean_enq_count = _count_table(conn, "clean_enquiries")
            merge_count = _count_table(conn, "merge_audit_log")
            if merge_count > 0:
                merge_preview = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT canonical_enquiry_id, merged_enquiry_id, account_key,
                               product_family, canonical_t0_time, merged_t0_time, merge_reason
                        FROM merge_audit_log
                        ORDER BY id DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]

            steward_count = _count_table(conn, "steward_review_queue")
            if steward_count > 0:
                steward_preview = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT enquiry_id_a, enquiry_id_b, account_key,
                               product_family, conflict_reason, status, created_at
                        FROM steward_review_queue
                        ORDER BY id DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]

            blocked_count = _count_table(conn, "blocked_accounts")
            if blocked_count > 0:
                blocked_preview = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT id, enquiry_id, account_key, reason, blocked_at, created_by
                        FROM blocked_accounts
                        ORDER BY id DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]

    source_age_hours: float | None = None
    stale_locked = True
    try:
        _, _, _, source_age_hours, _, _, _ = load_enquiries_and_meta()
        stale_locked = source_age_hours is None or source_age_hours > STALE_HOLD_HOURS
    except Exception:
        source_age_hours = None
        stale_locked = True

    do_not_use_present = [c for c in raw_enq_cols if "DO_NOT_USE" in c]
    math_check_ok = (raw_enq_count - clean_enq_count == merge_count) if clean_enq_count > 0 else False

    return {
        "env_mode": get_env_mode(),
        "dev_sandbox_enabled": get_env_mode() == "development",
        "db_exists": db_path.exists(),
        "csv_files": {
            "enquiries_exists": enq_csv.exists(),
            "enquiries_rows": enq_csv_rows,
            "outcomes_exists": out_csv.exists(),
            "outcomes_rows": out_csv_rows,
        },
        "raw_enquiries": {
            "row_count": raw_enq_count,
            "columns_count": len(raw_enq_cols),
            "columns": raw_enq_cols,
            "do_not_use_columns_present": do_not_use_present,
            "pii_and_leakage_stripped": len(do_not_use_present) == 0 and raw_enq_count > 0,
        },
        "raw_outcomes": {
            "row_count": raw_out_count,
            "label_counts": label_counts,
            "unknown_count": int(label_counts.get("Unknown", 0)),
        },
        "deduplication": {
            "raw_count": raw_enq_count,
            "clean_count": clean_enq_count,
            "merged_count": merge_count,
            "math_check_ok": math_check_ok,
            "merge_audit_preview": merge_preview,
            "steward_review_count": steward_count,
            "steward_review_preview": steward_preview,
        },
        "quarantine": {
            "enquiries_count": q_enq_count,
            "outcomes_count": q_out_count,
            "preview": [
                {
                    "enquiry_id": r.get("enquiry_id", ""),
                    "account_key": r.get("account_key", ""),
                    "t0_time": r.get("t0_time", ""),
                    "product_family": r.get("product_family", ""),
                    "quarantine_reason": r.get("quarantine_reason", ""),
                }
                for r in q_enq_preview
            ],
        },
        "freshness": {
            "source_age_hours": source_age_hours,
            "stale_locked": stale_locked,
            "status": STALE_STATUS if stale_locked else "OK",
        },
        "blocked_accounts": {
            "count": blocked_count,
            "preview": blocked_preview,
        },
    }


@router.get("/api/v1/pipeline/us03-audit")
def run_us03_audit_endpoint(
    _auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """PLPT-11 / US-03: Chạy kiểm toán 2 file CSV đầu vào và trả kết quả cấu trúc JSON cho trình duyệt."""
    enq_path = PROJECT_ROOT / "data" / "raw" / "enquiries_snapshot.csv"
    out_path = PROJECT_ROOT / "data" / "raw" / "outcome_events.csv"
    if not enq_path.exists() or not out_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Chưa có file CSV trong data/raw/. Hãy nạp file hoặc chạy Sinh dữ liệu giả lập (DEV).",
        )

    enq = pd.read_csv(enq_path, dtype=str)
    out = pd.read_csv(out_path, dtype=str)
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    missing_enq = sorted(set(ENQUIRY_REQUIRED) - set(enq.columns))
    missing_out = sorted(set(OUTCOME_REQUIRED) - set(out.columns))
    schema_ok = not missing_enq and not missing_out
    add_check(
        "Schema",
        schema_ok,
        f"Đủ {len(ENQUIRY_REQUIRED)} cột Enquiry Snapshot và {len(OUTCOME_REQUIRED)} cột Outcome Events."
        if schema_ok
        else f"Thiếu cột Enquiry={missing_enq}; Outcome={missing_out}",
    )
    if not schema_ok:
        return {
            "pass_count": 0,
            "fail_count": 1,
            "quarantine_count": 0,
            "checks": checks,
        }

    for df in (enq, out):
        for col in df.columns:
            df[col] = df[col].astype("string").str.strip()

    enq["t0_time"] = pd.to_datetime(enq["t0_time"], errors="coerce", utc=False)
    enq["feature_event_time"] = pd.to_datetime(enq["feature_event_time"], errors="coerce", utc=False)
    enq["feature_available_at"] = pd.to_datetime(enq["feature_available_at"], errors="coerce", utc=False)
    out["observation_window_days"] = pd.to_numeric(out["observation_window_days"], errors="coerce")

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
    add_check(
        "Quarantine detection",
        True,
        f"Phát hiện và tách an toàn {quarantine_count} dòng lỗi ({', '.join(sorted(bad_ids)[:5])}...).",
    )

    enq = enq[~bad_mask].copy()
    out = out[~out["enquiry_id"].isin(bad_ids)].copy()

    ids_unique = int(enq["enquiry_id"].duplicated().sum()) == 0 and int(out["enquiry_id"].duplicated().sum()) == 0
    add_check("Unique enquiry_id", ids_unique, "Mỗi enquiry_id chỉ xuất hiện duy nhất một lần trong từng file.")

    merged = enq.merge(out, on="enquiry_id", how="outer", suffixes=("_enq", "_out"), indicator=True)
    unmatched = int((merged["_merge"] != "both").sum())
    add_check("One-to-one join", unmatched == 0, f"Ghép 1-1 đạt {len(merged) - unmatched}/{len(merged)} hồ sơ hợp lệ.")
    merged = merged[merged["_merge"] == "both"].copy()

    cohort_mismatch = int((merged["cohort_enq"] != merged["cohort_out"]).sum())
    add_check("Cohort consistency", cohort_mismatch == 0, f"Sai lệch cohort giữa 2 file: {cohort_mismatch}.")

    train_mature = merged[(merged["cohort_enq"] == "TRAINING_CANDIDATE") & (merged["maturity_state"] == "MATURE")]
    train_p = train_mature[train_mature["label"] == "POSITIVE"]
    train_n = train_mature[train_mature["label"] == "NEGATIVE"]
    pos_ev_ok = (
        train_p["business_need_confirmed"].eq("Yes")
        & train_p["product_fit_confirmed"].eq("Yes")
        & train_p["agreed_next_step_confirmed"].eq("Yes")
    ).all()
    neg_cov_ok = (
        train_n["observer_coverage"].eq("Complete")
        & train_n["observation_window_days"].eq(60)
    ).all()
    ac1_ok = len(train_mature) >= 1000 and len(train_p) >= 100 and len(train_n) >= 100 and pos_ev_ok and neg_cov_ok
    add_check(
        "AC1 Volume Floor",
        ac1_ok,
        f"Train mature={len(train_mature)} (P={len(train_p)}, N={len(train_n)}), đủ 3 bằng chứng Rubric & 60 ngày.",
    )

    test_mature = merged[(merged["cohort_enq"] == "SEALED_LATER_TEST") & (merged["maturity_state"] == "MATURE")]
    test_p = int((test_mature["label"] == "POSITIVE").sum())
    test_n = int((test_mature["label"] == "NEGATIVE").sum())
    train_max = train_mature["t0_time"].max()
    test_min = test_mature["t0_time"].min()
    temporal_ok = pd.notna(train_max) and pd.notna(test_min) and train_max < test_min
    ac2_ok = len(test_mature) >= 200 and test_p >= 40 and test_n >= 40 and temporal_ok
    add_check(
        "AC2 Independent Later Test Cohort",
        ac2_ok,
        f"Test mature={len(test_mature)} (P={test_p}, N={test_n}), train_t0_max < test_t0_min={temporal_ok}.",
    )

    ev_invalid = int((enq["t0_time"].isna() | enq["feature_event_time"].isna() | (enq["feature_event_time"] > enq["t0_time"])).sum())
    av_invalid = int((enq["t0_time"].isna() | enq["feature_available_at"].isna() | (enq["feature_available_at"] > enq["t0_time"])).sum())
    add_check("AC3 Feature event time", ev_invalid == 0, f"Vi phạm feature_event_time > t0_time: {ev_invalid}.")
    add_check("AC3 Feature availability time", av_invalid == 0, f"Vi phạm feature_available_at > t0_time: {av_invalid}.")

    eligible = merged[merged["maturity_state"] == "MATURE"]
    known = eligible[eligible["label"].isin(["POSITIVE", "NEGATIVE"])]
    unknown_open = merged[merged["label"] == "Unknown"]
    coverage = round(len(known) / len(eligible) * 100.0, 2) if len(eligible) else 0.0
    add_check(
        "AC4 Label coverage",
        coverage >= 90.0,
        f"Độ bao phủ nhãn trưởng thành: {coverage:.2f}% (ngưỡng >= 90%); hồ sơ Unknown: {len(unknown_open)}.",
    )

    forbidden_cols = [c for c in enq.columns if c.endswith("_DO_NOT_USE")]
    pass_count = sum(1 for c in checks if c["status"] == "PASS")
    fail_count = sum(1 for c in checks if c["status"] == "FAIL")

    return {
        "enquiry_rows_total": int(len(enq) + quarantine_count),
        "outcome_rows_total": int(len(out) + quarantine_count),
        "quarantine_count": quarantine_count,
        "forbidden_columns_detected": forbidden_cols,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "checks": checks,
    }


@router.post("/api/v1/pipeline/ingest")
def trigger_pipeline_ingest(
    auth: AuthContext | None = Depends(
        require_roles({"DATA_STEWARD", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-14 (US-06): Nạp 2 file CSV chuẩn UTF-8 vào SQLite, tách Quarantine, lọc 3 cột *_DO_NOT_USE (Idempotent)."""
    db_path = get_db_path()
    before_enq = 0
    before_out = 0
    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            before_enq = _count_table(conn, "raw_enquiries")
            before_out = _count_table(conn, "raw_outcomes")

    try:
        _run_csv_validator_for_active_paths()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    after_enq = 0
    after_out = 0
    do_not_use_cols: list[str] = []
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        after_enq = _count_table(conn, "raw_enquiries")
        after_out = _count_table(conn, "raw_outcomes")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_enquiries)").fetchall()]
        do_not_use_cols = [c for c in cols if "DO_NOT_USE" in c]

    q_enq_count, _ = _read_csv_preview(get_quarantine_dir() / "quarantine_rows.csv", limit=0)
    q_out_count, _ = _read_csv_preview(get_quarantine_dir() / "quarantine_outcomes.csv", limit=0)
    delta_enq = after_enq - before_enq
    delta_out = after_out - before_out

    record_security_audit_log(
        db_path,
        event_type="PIPELINE_CSV_INGEST",
        user_id=auth.user_id if auth else "DATA_STEWARD",
        role=auth.role if auth else "DATA_STEWARD",
        endpoint="/api/v1/pipeline/ingest",
        status_code=200,
        detail=f"raw_enquiries={after_enq} (delta={delta_enq}), quarantine={q_enq_count}",
    )

    return {
        "status": "OK",
        "before_raw_enquiries": before_enq,
        "after_raw_enquiries": after_enq,
        "delta_raw_enquiries": delta_enq,
        "before_raw_outcomes": before_out,
        "after_raw_outcomes": after_out,
        "delta_raw_outcomes": delta_out,
        "idempotent_no_duplicates": before_enq > 0 and delta_enq == 0 and delta_out == 0,
        "quarantine_enquiries": q_enq_count,
        "quarantine_outcomes": q_out_count,
        "do_not_use_columns_present": do_not_use_cols,
    }


@router.post("/api/v1/pipeline/deduplicate")
def trigger_pipeline_deduplicate(
    auth: AuthContext | None = Depends(
        require_roles({"DATA_STEWARD", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """PLPT-15 (US-07): Chạy gộp trùng đa kênh -> clean_enquiries, merge_audit_log & steward_review_queue."""
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=400, detail="Chưa có cơ sở dữ liệu; hãy chạy Nạp CSV trước.")
    summary = deduplicator.deduplicate_enquiries(db_path=db_path)
    record_security_audit_log(
        db_path,
        event_type="PIPELINE_DEDUPLICATE",
        user_id=auth.user_id if auth else "DATA_STEWARD",
        role=auth.role if auth else "DATA_STEWARD",
        endpoint="/api/v1/pipeline/deduplicate",
        status_code=200,
        detail=f"raw={summary['raw_count']}, clean={summary['clean_count']}, merged={summary['merged_count']}",
    )
    return summary


@router.post("/api/v1/dev-sandbox/generate-synthetic")
def dev_generate_synthetic_data(
    auth: AuthContext | None = Depends(
        require_roles({"DATA_STEWARD", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """[DEV ONLY] Sinh bộ dữ liệu giả lập SYNTHETIC_ONLY (1.260 dòng) phục vụ Demo Sprint 2."""
    _require_dev_mode()
    generate_synthetic_data.main()
    enq_rows, _ = _read_csv_preview(PROJECT_ROOT / "data" / "raw" / "enquiries_snapshot.csv", limit=0)
    out_rows, _ = _read_csv_preview(PROJECT_ROOT / "data" / "raw" / "outcome_events.csv", limit=0)
    record_security_audit_log(
        get_db_path(),
        event_type="DEV_GENERATE_SYNTHETIC",
        user_id=auth.user_id if auth else "DATA_STEWARD",
        role=auth.role if auth else "DATA_STEWARD",
        endpoint="/api/v1/dev-sandbox/generate-synthetic",
        status_code=200,
        detail=f"SYNTHETIC_ONLY enq={enq_rows}, out={out_rows}",
    )
    return {
        "status": "OK",
        "data_origin": "SYNTHETIC_ONLY",
        "enquiries_rows": enq_rows,
        "outcomes_rows": out_rows,
    }


@router.post("/api/v1/dev-sandbox/simulate-stale")
def dev_simulate_stale_data(
    payload: dict[str, Any] = Body(default={}),
    auth: AuthContext | None = Depends(
        require_roles({"SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """[DEV ONLY] PLPT-17 Bước 4.4: Đẩy extraction_time lùi > 24h để kiểm chứng khóa HOLD_STALE_DATA."""
    _require_dev_mode()
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=400, detail="Database chưa tồn tại.")
    stale_ts = str(payload.get("extraction_time") or "2025-01-01T08:00:00+07:00").strip()
    updated = 0
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            has_clean = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clean_enquiries'"
            ).fetchone()
            if not has_clean:
                raise HTTPException(status_code=400, detail="Bảng clean_enquiries chưa tồn tại.")
            updated = int(
                conn.execute("UPDATE clean_enquiries SET extraction_time = ?", (stale_ts,)).rowcount
            )
            conn.commit()
    record_security_audit_log(
        db_path,
        event_type="DEV_SIMULATE_STALE",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/simulate-stale",
        status_code=200,
        detail=f"updated={updated}, extraction_time={stale_ts}",
    )
    return {
        "status": STALE_STATUS,
        "updated_rows": updated,
        "extraction_time": stale_ts,
    }


@router.post("/api/v1/dev-sandbox/restore-fresh")
def dev_restore_fresh_data(
    auth: AuthContext | None = Depends(
        require_roles({"DATA_STEWARD", "SUPERVISOR", "ADMINISTRATOR"}, enforce_flag_fn=is_rbac_enforced_on_v1)
    ),
) -> dict[str, Any]:
    """[DEV ONLY] Khôi phục dữ liệu tươi (< 24h) bằng chuỗi generate -> ingest -> deduplicate."""
    _require_dev_mode()
    generate_synthetic_data.main()
    _run_csv_validator_for_active_paths()
    dedup_summary = deduplicator.deduplicate_enquiries(db_path=get_db_path())
    record_security_audit_log(
        get_db_path(),
        event_type="DEV_RESTORE_FRESH",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/restore-fresh",
        status_code=200,
        detail=f"clean={dedup_summary['clean_count']}, merged={dedup_summary['merged_count']}",
    )
    return {
        "status": "OK",
        "restored_at": datetime.now(VN_TZ).replace(microsecond=0).isoformat(),
        "deduplication": dedup_summary,
    }


@router.post("/api/v1/dev-sandbox/run-sprint2-check")
def dev_run_sprint2_check(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    [DEV ONLY] Chạy bộ kiểm thử Sprint 2 nằm trong allowlist cố định (`shell=False`, không nhận tham số shell tự do).
    Khóa cứng HTTP 403 ở ENV_MODE=evaluation/production.
    """
    _require_dev_mode()
    suite_id = str(payload.get("suite_id") or "").strip()
    spec = ALLOWED_SPRINT2_CHECKS.get(suite_id)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"suite_id không hợp lệ. Chỉ chấp nhận: {sorted(ALLOWED_SPRINT2_CHECKS.keys())}",
        )

    outputs: list[str] = []
    overall_passed = True

    if spec["kind"] == "pytest":
        cmd = [sys.executable, "-m", "pytest", *spec["targets"], "-v", "--tb=short"]
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            shell=False,
        )
        overall_passed = proc.returncode == 0
        outputs.append((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""))
    elif spec["kind"] == "pre_commit":
        pre_commit_bin = Path(sys.executable).parent / (
            "pre-commit.exe" if sys.platform.startswith("win") else "pre-commit"
        )
        exe = str(pre_commit_bin) if pre_commit_bin.exists() else "pre-commit"
        for hook_id in spec["hooks"]:
            try:
                proc = subprocess.run(
                    [exe, "run", hook_id, "--all-files"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    shell=False,
                )
                if proc.returncode != 0:
                    overall_passed = False
                outputs.append(f"=== pre-commit run {hook_id} --all-files (exit={proc.returncode}) ===\n{proc.stdout}{proc.stderr}")
            except FileNotFoundError:
                overall_passed = False
                outputs.append(f"Không tìm thấy lệnh pre-commit cho hook {hook_id}.")

    record_security_audit_log(
        get_db_path(),
        event_type=f"DEV_CHECK_{suite_id.upper()}",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/run-sprint2-check",
        status_code=200 if overall_passed else 500,
        detail=f"suite_id={suite_id}, passed={overall_passed}",
    )

    return {
        "suite_id": suite_id,
        "label": spec["label"],
        "passed": overall_passed,
        "output": "\n".join(outputs).strip(),
    }
