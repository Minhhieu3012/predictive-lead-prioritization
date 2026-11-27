"""Sprint 3 Browser Console Routes: Workspace Isolation, Manifest, 40 Control Fixtures & Gate G1 (PLPT-24..28)."""

import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from src.security.auth import AuthContext, get_env_mode, record_security_audit_log
from src.security.rbac import is_rbac_enforced_on_v1, require_roles
from src.services.queue_service import (
    STALE_HOLD_HOURS,
    STALE_STATUS,
    ensure_operational_tables,
    get_db_path,
    load_enquiries_and_meta,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "artifacts" / "models" / "plpt-logreg-v1.0.0" / "manifest.json"
CONTROL_FIXTURES_REPORT_PATH = PROJECT_ROOT / "artifacts" / "gate_g1" / "control_fixtures_report.json"
GATE_G1_REPORT_PATH = PROJECT_ROOT / "artifacts" / "gate_g1" / "gate_g1_benchmark_report.json"
SEALED_LOCKS_PATH = PROJECT_ROOT / "artifacts" / "gate_g1" / "sealed_cohort_locks.json"

PROHIBITED_FEATURE_NAMES = {
    "enquiry_id",
    "account_key",
    "contact_phone",
    "contact_email",
    "phone",
    "email",
    "customer_name",
}

ALLOWED_SPRINT3_CHECKS: dict[str, dict[str, Any]] = {
    "plpt24_workspace": {
        "label": "PLPT-24: pytest test_sprint3_workspace.py",
        "kind": "pytest",
        "targets": ["tests/workspace_pilot/test_sprint3_workspace.py"],
    },
    "plpt57_perf_p95": {
        "label": "PLPT-57: pytest test_plpt57_pilot_and_perf.py (p95 <= 3.0s)",
        "kind": "pytest",
        "targets": ["tests/workspace_pilot/test_plpt57_pilot_and_perf.py"],
    },
    "plpt26_ml_eval": {
        "label": "PLPT-26: pytest test_sprint3_ml_eval.py + test_plpt56_shadow_and_fallback.py",
        "kind": "pytest",
        "targets": [
            "tests/ml_shadow/test_sprint3_ml_eval.py",
            "tests/ml_shadow/test_plpt56_shadow_and_fallback.py",
        ],
    },
    "plpt27_cf40_script": {
        "label": "PLPT-27: scripts/audit/run_control_fixtures.py (40 Control Fixtures)",
        "kind": "script",
        "script": "scripts/audit/run_control_fixtures.py",
    },
    "plpt27_cf40_pytest": {
        "label": "PLPT-27: pytest tests/control_fixtures/ (CF-01..CF-40 + CF-09)",
        "kind": "pytest",
        "targets": ["tests/control_fixtures/"],
    },
    "plpt28_single_use_verify": {
        "label": "PLPT-28: Kiểm chứng chốt khóa Single-Use (run_gate_g1_eval.py)",
        "kind": "gate_g1_lock_verify",
        "script": "scripts/audit/run_gate_g1_eval.py",
    },
    "plpt28_demo_fresh_eval": {
        "label": "PLPT-28: Demo chạy lại Gate G1 trên SYNTHETIC (tự sao lưu & khôi phục khóa)",
        "kind": "gate_g1_demo_fresh",
        "script": "scripts/audit/run_gate_g1_eval.py",
    },
    "sprint3_dod_all": {
        "label": "DoD Sprint 3: Toàn bộ pytest Workspace + ML + Control Fixtures + run_control_fixtures.py",
        "kind": "sprint3_dod",
        "targets": [
            "tests/workspace_pilot/test_sprint3_workspace.py",
            "tests/ml_shadow/test_sprint3_ml_eval.py",
            "tests/control_fixtures/",
        ],
        "script": "scripts/audit/run_control_fixtures.py",
    },
}

router = APIRouter(tags=["Sprint 3 Gate G1 & QA Console"])


def _require_dev_mode() -> None:
    """SEC-06: Chặn tuyệt đối các thao tác chạy kiểm thử/demo sandbox ngoài ENV_MODE=development."""
    mode = get_env_mode()
    if mode != "development":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Thao tác QA/Demo Sandbox Sprint 3 bị khóa ở ENV_MODE={mode} "
                "(chỉ được phép chạy trong ENV_MODE=development theo SEC-06)."
            ),
        )


def _utf8_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _read_json_artifact(path: Path) -> tuple[bool, Any]:
    if not path.exists():
        return False, None
    try:
        return True, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, None


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    has_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not has_tbl:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _evaluate_manifest_checks(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {
            "exists": False,
            "feature_count": 0,
            "feature_count_ok": False,
            "prohibited_features_found": [],
            "no_prohibited_features": False,
            "non_llm_candidate_ok": False,
            "numerical_tolerance_ok": False,
            "serving_mode_locked_rules": False,
            "all_passed": False,
        }

    features = [str(c) for c in (manifest.get("feature_columns_used") or [])]
    prohibited = [
        c
        for c in features
        if c in PROHIBITED_FEATURE_NAMES or "DO_NOT_USE" in c.upper() or "PHONE" in c.upper() or "EMAIL" in c.upper()
    ]
    cand = str(manifest.get("candidate_type") or "")
    sec_cand = str(manifest.get("secondary_candidate_type") or "")
    tol = manifest.get("numerical_tolerance")
    serving_locked = str(manifest.get("serving_mode_locked") or "")

    feature_count_ok = 0 < len(features) <= 30
    no_prohibited = len(prohibited) == 0
    non_llm_ok = (
        cand == "REGULARIZED_LOGISTIC_REGRESSION"
        and sec_cand == "DECISION_TREE_SHALLOW"
        and "LLM" not in cand.upper()
        and "GENAI" not in cand.upper()
    )
    tol_ok = isinstance(tol, (int, float)) and abs(float(tol) - 1e-6) < 1e-12
    serving_ok = serving_locked == "RULES_BASELINE"

    return {
        "exists": True,
        "feature_count": len(features),
        "feature_count_ok": feature_count_ok,
        "prohibited_features_found": prohibited,
        "no_prohibited_features": no_prohibited,
        "non_llm_candidate_ok": non_llm_ok,
        "numerical_tolerance_ok": tol_ok,
        "serving_mode_locked_rules": serving_ok,
        "all_passed": feature_count_ok and no_prohibited and non_llm_ok and tol_ok and serving_ok,
    }


@router.get("/api/v1/gate-g1/evidence")
def get_gate_g1_evidence(
    _auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    Tổng hợp toàn bộ bằng chứng kiểm thử Sprint 3 (PLPT-24..PLPT-28) trực tiếp cho trình duyệt:
    - Đối chiếu cô lập SQLite: staff_review_logs, staff_override_logs vs raw_outcomes (PLPT-25)
    - Model manifest & pre-t0 feature compliance (PLPT-26)
    - Báo cáo 40 Control Fixtures (PLPT-27)
    - Báo cáo Temporal Benchmark Gate G1 & Sealed Cohort Locks (PLPT-28)
    """
    db_path = get_db_path()
    review_count = 0
    override_count = 0
    raw_outcomes_count = 0
    raw_outcomes_labels: dict[str, int] = {}
    review_preview: list[dict[str, Any]] = []
    override_preview: list[dict[str, Any]] = []

    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                ensure_operational_tables(conn)

            review_count = _count_table(conn, "staff_review_logs")
            if review_count > 0:
                review_preview = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT id, enquiry_id, decision_type, contact_permission_verified,
                               contact_attempted, review_completed, reviewed_by, reviewed_at
                        FROM staff_review_logs
                        ORDER BY id DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]

            override_count = _count_table(conn, "staff_override_logs")
            if override_count > 0:
                override_preview = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT id, enquiry_id, override_reason_code, justification,
                               overridden_by, overridden_at
                        FROM staff_override_logs
                        ORDER BY id DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]

            raw_outcomes_count = _count_table(conn, "raw_outcomes")
            if raw_outcomes_count > 0:
                for r in conn.execute(
                    "SELECT COALESCE(label, 'Unknown') AS lbl, COUNT(*) AS cnt FROM raw_outcomes GROUP BY lbl"
                ).fetchall():
                    raw_outcomes_labels[str(r["lbl"])] = int(r["cnt"])

    source_age_hours: float | None = None
    stale_locked = True
    try:
        _, _, _, source_age_hours, _, _, _ = load_enquiries_and_meta()
        stale_locked = source_age_hours is None or source_age_hours > STALE_HOLD_HOURS
    except Exception:
        source_age_hours = None
        stale_locked = True

    manifest_exists, manifest_data = _read_json_artifact(MANIFEST_PATH)
    cf_exists, cf_data = _read_json_artifact(CONTROL_FIXTURES_REPORT_PATH)
    g1_exists, g1_data = _read_json_artifact(GATE_G1_REPORT_PATH)
    locks_exists, locks_data = _read_json_artifact(SEALED_LOCKS_PATH)

    manifest_checks = _evaluate_manifest_checks(manifest_data if manifest_exists else None)

    locks_rows: list[dict[str, Any]] = []
    if locks_exists and isinstance(locks_data, dict):
        for fp, meta in locks_data.items():
            if isinstance(meta, dict):
                locks_rows.append(
                    {
                        "fingerprint": str(fp),
                        "training_cutoff_t0": str(meta.get("training_cutoff_t0") or ""),
                        "row_count": meta.get("row_count"),
                        "locked_at": str(meta.get("locked_at") or ""),
                    }
                )

    cf_passed = bool(
        cf_exists
        and isinstance(cf_data, dict)
        and int(cf_data.get("passed_count") or 0) == 40
        and int(cf_data.get("failed_count") or 0) == 0
        and str(cf_data.get("gate_g1_controls_status") or "") == "PASSED"
    )
    g1_rules_locked = bool(
        g1_exists
        and isinstance(g1_data, dict)
        and str(g1_data.get("gate_g1_decision") or "") == "FALLBACK_RULES_BASELINE"
        and str(g1_data.get("active_serving_mode") or "") == "RULES_BASELINE"
        and manifest_checks["serving_mode_locked_rules"]
    )

    return {
        "env_mode": get_env_mode(),
        "dev_sandbox_enabled": get_env_mode() == "development",
        "freshness": {
            "source_age_hours": source_age_hours,
            "stale_locked": stale_locked,
            "status": STALE_STATUS if stale_locked else "OK",
        },
        "workspace_isolation": {
            "db_exists": db_path.exists(),
            "staff_review_logs_count": review_count,
            "staff_review_logs_preview": review_preview,
            "staff_override_logs_count": override_count,
            "staff_override_logs_preview": override_preview,
            "raw_outcomes_count": raw_outcomes_count,
            "raw_outcomes_labels": raw_outcomes_labels,
            "raw_outcomes_unknown_count": int(raw_outcomes_labels.get("Unknown", 0)),
            "isolation_guarantee": (
                "Bảng staff_review_logs và staff_override_logs lưu tách biệt hoàn toàn khỏi raw_outcomes "
                "(CF-29); thao tác nhân sự không bao giờ sửa nhãn huấn luyện hay kích hoạt CRM."
            ),
        },
        "manifest": {
            "path": "artifacts/models/plpt-logreg-v1.0.0/manifest.json",
            "exists": manifest_exists,
            "checks": manifest_checks,
            "data": manifest_data,
        },
        "control_fixtures": {
            "path": "artifacts/gate_g1/control_fixtures_report.json",
            "exists": cf_exists,
            "all_40_passed": cf_passed,
            "data": cf_data,
        },
        "gate_g1_benchmark": {
            "path": "artifacts/gate_g1/gate_g1_benchmark_report.json",
            "exists": g1_exists,
            "rules_fallback_locked": g1_rules_locked,
            "data": g1_data,
        },
        "sealed_cohort_locks": {
            "path": "artifacts/gate_g1/sealed_cohort_locks.json",
            "exists": locks_exists,
            "lock_count": len(locks_rows),
            "rows": locks_rows,
            "data": locks_data,
        },
        "dod_summary": {
            "control_fixtures_40_passed": cf_passed,
            "gate_g1_and_manifest_locked_rules": g1_rules_locked,
            "single_use_cohort_locked": len(locks_rows) > 0,
        },
    }


@router.post("/api/v1/dev-sandbox/run-sprint3-check")
def dev_run_sprint3_check(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    [DEV ONLY] Chạy bộ kiểm thử Sprint 3 trong allowlist cố định (`shell=False`).
    Bị khóa cứng HTTP 403 ngoài ENV_MODE=development theo SEC-06.
    """
    _require_dev_mode()
    suite_id = str(payload.get("suite_id") or "").strip()
    spec = ALLOWED_SPRINT3_CHECKS.get(suite_id)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"suite_id không hợp lệ. Chỉ chấp nhận: {sorted(ALLOWED_SPRINT3_CHECKS.keys())}",
        )

    env = _utf8_subprocess_env()
    outputs: list[str] = []
    overall_passed = True
    extra: dict[str, Any] = {}

    kind = spec["kind"]
    if kind == "pytest":
        cmd = [sys.executable, "-m", "pytest", *spec["targets"], "-v", "--tb=short"]
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
        overall_passed = proc.returncode == 0
        outputs.append((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""))

    elif kind == "script":
        script_path = PROJECT_ROOT / spec["script"]
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
        overall_passed = proc.returncode == 0
        outputs.append((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""))

    elif kind == "gate_g1_lock_verify":
        script_path = PROJECT_ROOT / spec["script"]
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        lock_triggered = proc.returncode != 0 and "SealedCohortAlreadyUsedError" in combined
        overall_passed = lock_triggered
        extra["single_use_lock_verified"] = lock_triggered
        if lock_triggered:
            outputs.append(
                "[ĐẠT CHỐT KHÓA SINGLE-USE (RFP MỤC 8)]\n"
                "Hệ thống đã cố ý chặn tái đánh giá tập test đã niêm phong bằng ngoại lệ SealedCohortAlreadyUsedError.\n\n"
                "--- Output thực tế từ run_gate_g1_eval.py ---\n"
                + combined
            )
        else:
            outputs.append(
                "[CẢNH BÁO] Không ghi nhận ngoại lệ SealedCohortAlreadyUsedError như kỳ vọng.\n\n"
                + combined
            )

    elif kind == "gate_g1_demo_fresh":
        script_path = PROJECT_ROOT / spec["script"]
        lock_existed = SEALED_LOCKS_PATH.exists()
        orig_lock_content = SEALED_LOCKS_PATH.read_text(encoding="utf-8") if lock_existed else None
        try:
            SEALED_LOCKS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SEALED_LOCKS_PATH.write_text("{}", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                shell=False,
            )
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            overall_passed = (
                proc.returncode == 0
                and "decision=FALLBACK_RULES_BASELINE" in combined
                and "mode=RULES_BASELINE" in combined
            )
            extra["lock_restored"] = True
            outputs.append(
                "[DEMO SYNTHETIC] Đã sao lưu khóa tạm, chạy lại run_gate_g1_eval.py và tự động khôi phục sealed_cohort_locks.json.\n\n"
                + combined
            )
        finally:
            if lock_existed and orig_lock_content is not None:
                SEALED_LOCKS_PATH.write_text(orig_lock_content, encoding="utf-8")
            elif not lock_existed and SEALED_LOCKS_PATH.exists():
                SEALED_LOCKS_PATH.unlink()

    elif kind == "sprint3_dod":
        p1 = subprocess.run(
            [sys.executable, "-m", "pytest", *spec["targets"], "-v", "--tb=short"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=150,
            shell=False,
        )
        p2 = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / spec["script"])],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
        )
        overall_passed = (p1.returncode == 0) and (p2.returncode == 0)
        outputs.append(
            f"=== [1/2] pytest Sprint 3 (exit={p1.returncode}) ===\n{p1.stdout}{p1.stderr}\n"
            f"=== [2/2] run_control_fixtures.py (exit={p2.returncode}) ===\n{p2.stdout}{p2.stderr}"
        )

    record_security_audit_log(
        get_db_path(),
        event_type=f"DEV_CHECK_{suite_id.upper()}",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/run-sprint3-check",
        status_code=200 if overall_passed else 500,
        detail=f"suite_id={suite_id}, passed={overall_passed}",
    )

    return {
        "suite_id": suite_id,
        "label": spec["label"],
        "passed": overall_passed,
        "output": "\n".join(outputs).strip(),
        **extra,
    }
