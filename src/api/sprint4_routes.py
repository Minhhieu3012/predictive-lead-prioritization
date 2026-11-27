"""Sprint 4 Browser Console Routes: Security, Incident Hold, Shadow Fallback, Pilot 20BD & Drift Monitor (PLPT-55..58)."""

import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from src.ingest import csv_validator, deduplicator
from src.models.shadow_runner import ensure_batch_run_table, ensure_shadow_score_table
from src.monitoring.drift_monitor import ensure_drift_tables
from src.pilot.pilot_tracker import ensure_pilot_tables
from src.security.auth import AuthContext, ensure_auth_tables, get_env_mode, record_security_audit_log
from src.security.incident_hold import ensure_incident_tables, get_active_incident_hold
from src.security.rbac import is_rbac_enforced_on_v1, require_roles
from src.services.queue_service import (
    STALE_HOLD_HOURS,
    STALE_STATUS,
    ensure_operational_tables,
    get_db_path,
    get_quarantine_dir,
    load_enquiries_and_meta,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
REQUIRED_ENV_EXAMPLE_KEYS = (
    "ENV_MODE",
    "DATABASE_PATH",
    "SESSION_SECRET_KEY",
    "SESSION_TTL_MINUTES",
)

ALLOWED_SPRINT4_CHECKS: dict[str, dict[str, Any]] = {
    "plpt55_rbac_incident": {
        "label": "PLPT-55: pytest test_plpt55_rbac_and_incident.py",
        "kind": "pytest",
        "args": ["tests/security_ops/test_plpt55_rbac_and_incident.py", "-v", "--tb=short"],
    },
    "plpt55_git_secrets": {
        "label": "PLPT-55: pre-commit gitleaks + forbid-real-env-files",
        "kind": "pre_commit",
        "hooks": ["gitleaks", "forbid-real-env-files"],
    },
    "plpt56_shadow": {
        "label": "PLPT-56: pytest test_plpt56_shadow_and_fallback.py",
        "kind": "pytest",
        "args": ["tests/ml_shadow/test_plpt56_shadow_and_fallback.py", "-v", "--tb=short"],
    },
    "plpt57_pilot_perf": {
        "label": "PLPT-57: pytest test_plpt57_pilot_and_perf.py (100 req / 5 luồng, p95 <= 3.0s)",
        "kind": "pytest",
        "args": ["tests/workspace_pilot/test_plpt57_pilot_and_perf.py", "-v", "--tb=short"],
    },
    "plpt58_drift_only": {
        "label": "PLPT-58: pytest test_plpt58_drift_and_ops.py -k drift (Ranh giới Sprint 4)",
        "kind": "pytest",
        "args": ["tests/security_ops/test_plpt58_drift_and_ops.py", "-k", "drift", "-v", "--tb=short"],
    },
    "sprint4_four_suites": {
        "label": "DoD Sprint 4 (5.1): Chạy 4 bộ test Sprint 4 (PLPT-55..PLPT-58)",
        "kind": "pytest",
        "args": [
            "tests/security_ops/test_plpt55_rbac_and_incident.py",
            "tests/ml_shadow/test_plpt56_shadow_and_fallback.py",
            "tests/workspace_pilot/test_plpt57_pilot_and_perf.py",
            "tests/security_ops/test_plpt58_drift_and_ops.py",
            "-v",
            "--tb=short",
        ],
    },
    "sprint4_full_regression": {
        "label": "DoD Sprint 4 (5.2): Hồi quy toàn bộ dự án (pytest -v) + 40 Control Fixtures",
        "kind": "full_regression",
        "script": "scripts/audit/run_control_fixtures.py",
    },
    "sprint4_gitleaks": {
        "label": "DoD Sprint 4 (5.3): pre-commit run gitleaks --all-files",
        "kind": "pre_commit",
        "hooks": ["gitleaks"],
    },
}

router = APIRouter(tags=["Sprint 4 Security & Ops Console"])


def _require_dev_mode() -> None:
    """SEC-06: Chặn tuyệt đối các thao tác giả lập/test sandbox ngoài ENV_MODE=development."""
    mode = get_env_mode()
    if mode != "development":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Thao tác QA/Demo Sandbox Sprint 4 bị khóa ở ENV_MODE={mode} "
                "(chỉ được phép chạy trong ENV_MODE=development theo SEC-06)."
            ),
        )


def _utf8_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    has_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if not has_tbl:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _read_env_example_status() -> dict[str, Any]:
    if not ENV_EXAMPLE_PATH.exists():
        return {
            "exists": False,
            "all_required_present": False,
            "missing_keys": list(REQUIRED_ENV_EXAMPLE_KEYS),
            "present_keys": [],
            "content": "",
        }
    content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    present: list[str] = []
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key:
            present.append(key)
    missing = [k for k in REQUIRED_ENV_EXAMPLE_KEYS if k not in present]
    return {
        "exists": True,
        "all_required_present": len(missing) == 0,
        "missing_keys": missing,
        "present_keys": present,
        "content": content,
    }


def _run_csv_validator_for_active_paths() -> None:
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


@router.get("/api/v1/sprint4/overview")
def get_sprint4_overview(
    _auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    Tổng hợp toàn bộ bảng SQLite & metadata phục vụ kiểm chứng Sprint 4 trên trình duyệt
    (thay thế việc mở SQLite Viewer cho revoked_sessions, security_audit_logs, incident_holds,
    pilot_assignments, exposure_logs, daily_drift_snapshots, drift_investigation_alerts).
    """
    db_path = get_db_path()

    revoked_count = 0
    revoked_preview: list[dict[str, Any]] = []
    audit_count = 0
    audit_preview: list[dict[str, Any]] = []
    incident_history: list[dict[str, Any]] = []

    shadow_count = 0
    batch_preview: list[dict[str, Any]] = []

    pilot_assign_count = 0
    pilot_assign_preview: list[dict[str, Any]] = []
    pilot_conflicting_accounts = 0
    exposure_count = 0
    exposure_preview: list[dict[str, Any]] = []
    pilot_effort_count = 0
    pilot_effort_preview: list[dict[str, Any]] = []
    override_count = 0
    raw_outcomes_count = 0

    drift_snap_count = 0
    drift_snap_preview: list[dict[str, Any]] = []
    drift_alert_count = 0
    drift_alert_preview: list[dict[str, Any]] = []

    if db_path.exists():
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                ensure_auth_tables(conn)
                ensure_incident_tables(conn)
                ensure_operational_tables(conn)
                ensure_shadow_score_table(conn)
                ensure_batch_run_table(conn)
                ensure_pilot_tables(conn)
                ensure_drift_tables(conn)

            revoked_count = _count_table(conn, "revoked_sessions")
            revoked_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT session_id, user_id, revoked_at, reason
                    FROM revoked_sessions
                    ORDER BY revoked_at DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            audit_count = _count_table(conn, "security_audit_logs")
            audit_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, event_type, user_id, role, endpoint, status_code, detail, created_at
                    FROM security_audit_logs
                    ORDER BY id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            incident_history = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT incident_id, status, severity, reason, triggered_by, triggered_at,
                           notify_deadline_at, purchaser_notified_at, within_4h_sla, released_at
                    FROM incident_holds
                    ORDER BY incident_id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            shadow_count = _count_table(conn, "shadow_score_logs")
            batch_preview = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM batch_run_logs ORDER BY id DESC LIMIT 10"
                ).fetchall()
            ]

            pilot_assign_count = _count_table(conn, "pilot_assignments")
            pilot_assign_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, assignment_key, account_key, treatment_arm, assigned_at
                    FROM pilot_assignments
                    ORDER BY id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]
            conflict_row = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT account_key
                    FROM pilot_assignments
                    WHERE COALESCE(account_key, '') != ''
                    GROUP BY UPPER(account_key)
                    HAVING COUNT(DISTINCT treatment_arm) > 1
                )
                """
            ).fetchone()
            pilot_conflicting_accounts = int(conflict_row[0]) if conflict_row else 0

            exposure_count = _count_table(conn, "exposure_logs")
            exposure_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, enquiry_id, account_key, staff_id, treatment_arm, scoring_mode, exposed_at
                    FROM exposure_logs
                    ORDER BY id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            pilot_effort_count = _count_table(conn, "pilot_effort_logs")
            pilot_effort_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, business_date, staff_id, treatment_arm, enquiries_reviewed,
                           queue_prep_minutes, triage_duration_seconds, due_total, due_on_time, logged_at
                    FROM pilot_effort_logs
                    ORDER BY id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            override_count = _count_table(conn, "staff_override_logs")
            raw_outcomes_count = _count_table(conn, "raw_outcomes")

            drift_snap_count = _count_table(conn, "daily_drift_snapshots")
            drift_snap_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, as_of_date, daily_volume, score_coverage_pct, unscored_count,
                           investigation_triggered, recommended_fallback_to_rules, auto_retrain_allowed, created_at
                    FROM daily_drift_snapshots
                    ORDER BY id DESC
                    LIMIT 15
                    """
                ).fetchall()
            ]

            drift_alert_count = _count_table(conn, "drift_investigation_alerts")
            drift_alert_preview = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, snapshot_id, as_of_date, alert_code, column_name, detail, status, created_at
                    FROM drift_investigation_alerts
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

    active_hold = get_active_incident_hold(db_path) if db_path.exists() else None
    manifest_path = PROJECT_ROOT / "artifacts" / "models" / "plpt-logreg-v1.0.0" / "manifest.json"
    manifest_serving_locked = None
    if manifest_path.exists():
        try:
            manifest_serving_locked = json.loads(manifest_path.read_text(encoding="utf-8")).get("serving_mode_locked")
        except (OSError, json.JSONDecodeError):
            manifest_serving_locked = None

    return {
        "env_mode": get_env_mode(),
        "dev_sandbox_enabled": get_env_mode() == "development",
        "freshness": {
            "source_age_hours": source_age_hours,
            "stale_locked": stale_locked,
            "status": STALE_STATUS if stale_locked else "OK",
        },
        "env_example": _read_env_example_status(),
        "security": {
            "active_incident_hold": active_hold,
            "incident_history": incident_history,
            "revoked_sessions_count": revoked_count,
            "revoked_sessions_preview": revoked_preview,
            "security_audit_logs_count": audit_count,
            "security_audit_logs_preview": audit_preview,
        },
        "shadow": {
            "shadow_score_logs_count": shadow_count,
            "batch_run_logs_preview": batch_preview,
            "manifest_serving_mode_locked": manifest_serving_locked,
        },
        "pilot": {
            "pilot_assignments_count": pilot_assign_count,
            "pilot_assignments_preview": pilot_assign_preview,
            "conflicting_accounts": pilot_conflicting_accounts,
            "exposure_logs_count": exposure_count,
            "exposure_logs_preview": exposure_preview,
            "pilot_effort_logs_count": pilot_effort_count,
            "pilot_effort_logs_preview": pilot_effort_preview,
            "staff_override_logs_count": override_count,
            "raw_outcomes_count": raw_outcomes_count,
        },
        "drift": {
            "daily_drift_snapshots_count": drift_snap_count,
            "daily_drift_snapshots_preview": drift_snap_preview,
            "drift_investigation_alerts_count": drift_alert_count,
            "drift_investigation_alerts_preview": drift_alert_preview,
        },
    }


@router.post("/api/v1/dev-sandbox/simulate-drift")
def dev_simulate_drift_spike(
    payload: dict[str, Any] = Body(default={}),
    auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """[DEV ONLY] Bước 4.3 (PLPT-58): Làm trống spec_completeness cho 200 dòng để kích hoạt MISSINGNESS_SPIKE & LOW_COVERAGE."""
    _require_dev_mode()
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPException(status_code=400, detail="Database chưa tồn tại.")

    limit_rows = int(payload.get("rows") or 200)
    limit_rows = max(1, min(limit_rows, 500))

    updated = 0
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            has_clean = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clean_enquiries'"
            ).fetchone()
            if not has_clean:
                raise HTTPException(status_code=400, detail="Bảng clean_enquiries chưa tồn tại.")
            updated = int(
                conn.execute(
                    "UPDATE clean_enquiries SET spec_completeness = '' "
                    "WHERE rowid IN (SELECT rowid FROM clean_enquiries LIMIT ?)",
                    (limit_rows,),
                ).rowcount
            )
            conn.commit()

    record_security_audit_log(
        db_path,
        event_type="DEV_SIMULATE_DRIFT",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/simulate-drift",
        status_code=200,
        detail=f"cleared spec_completeness on {updated} rows",
    )
    return {
        "status": "DRIFT_SIMULATED",
        "updated_rows": updated,
        "detail": f"Đã làm trống spec_completeness cho {updated} dòng để kích hoạt đồng thời MISSINGNESS_SPIKE (>= +10pp) và LOW_COVERAGE (< 90%).",
    }


@router.post("/api/v1/dev-sandbox/restore-clean-drift")
def dev_restore_clean_after_drift(
    auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """[DEV ONLY] Bước 4.3 (PLPT-58): Khôi phục dữ liệu sạch sau giả lập drift bằng csv_validator + deduplicator."""
    _require_dev_mode()
    try:
        _run_csv_validator_for_active_paths()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dedup_summary = deduplicator.deduplicate_enquiries(db_path=get_db_path())
    record_security_audit_log(
        get_db_path(),
        event_type="DEV_RESTORE_CLEAN_DRIFT",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/restore-clean-drift",
        status_code=200,
        detail=f"clean={dedup_summary['clean_count']}, merged={dedup_summary['merged_count']}",
    )
    return {
        "status": "OK",
        "deduplication": dedup_summary,
    }


@router.post("/api/v1/dev-sandbox/run-sprint4-check")
def dev_run_sprint4_check(
    payload: dict[str, Any] = Body(...),
    auth: AuthContext | None = Depends(
        require_roles(
            {"DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"},
            enforce_flag_fn=is_rbac_enforced_on_v1,
        )
    ),
) -> dict[str, Any]:
    """
    [DEV ONLY] Chạy bộ kiểm thử Sprint 4 nằm trong allowlist cố định (`shell=False`).
    Khóa cứng HTTP 403 ngoài ENV_MODE=development theo SEC-06.
    """
    _require_dev_mode()
    suite_id = str(payload.get("suite_id") or "").strip()
    spec = ALLOWED_SPRINT4_CHECKS.get(suite_id)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"suite_id không hợp lệ. Chỉ chấp nhận: {sorted(ALLOWED_SPRINT4_CHECKS.keys())}",
        )

    env = _utf8_subprocess_env()
    outputs: list[str] = []
    overall_passed = True

    kind = spec["kind"]
    if kind == "pytest":
        cmd = [sys.executable, "-m", "pytest", *spec["args"]]
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=150,
            shell=False,
        )
        overall_passed = proc.returncode == 0
        outputs.append((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""))

    elif kind == "pre_commit":
        pre_commit_bin = Path(sys.executable).parent / (
            "pre-commit.exe" if sys.platform.startswith("win") else "pre-commit"
        )
        exe = str(pre_commit_bin) if pre_commit_bin.exists() else "pre-commit"
        for hook_id in spec["hooks"]:
            try:
                proc = subprocess.run(
                    [exe, "run", hook_id, "--all-files"],
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    shell=False,
                )
                if proc.returncode != 0:
                    overall_passed = False
                outputs.append(
                    f"=== pre-commit run {hook_id} --all-files (exit={proc.returncode}) ===\n{proc.stdout}{proc.stderr}"
                )
            except FileNotFoundError:
                overall_passed = False
                outputs.append(f"Không tìm thấy lệnh pre-commit cho hook {hook_id}.")

    elif kind == "full_regression":
        p1 = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
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
            f"=== [1/2] Full Regression pytest -v (exit={p1.returncode}) ===\n{p1.stdout}{p1.stderr}\n"
            f"=== [2/2] run_control_fixtures.py (exit={p2.returncode}) ===\n{p2.stdout}{p2.stderr}"
        )

    record_security_audit_log(
        get_db_path(),
        event_type=f"DEV_CHECK_{suite_id.upper()}",
        user_id=auth.user_id if auth else "ADMINISTRATOR",
        role=auth.role if auth else "ADMINISTRATOR",
        endpoint="/api/v1/dev-sandbox/run-sprint4-check",
        status_code=200 if overall_passed else 500,
        detail=f"suite_id={suite_id}, passed={overall_passed}",
    )

    return {
        "suite_id": suite_id,
        "label": spec["label"],
        "passed": overall_passed,
        "output": "\n".join(outputs).strip(),
    }
