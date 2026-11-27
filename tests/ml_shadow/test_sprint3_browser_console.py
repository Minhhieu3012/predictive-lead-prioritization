"""Tests cho Sprint 3 Browser Console (PLPT-24..PLPT-28, DB Isolation, Gate G1 & SEC-06 DEV-ONLY guard)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app
from src.security.auth import DEFAULT_SESSION_STORE

VN_TZ = timezone(timedelta(hours=7))
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _login(client: TestClient, user_id: str, role: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"user_id": user_id, "role": role})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _seed_minimal_db(db_path: Path) -> None:
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE clean_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT,
                cohort TEXT,
                source TEXT,
                t0_time TEXT,
                extraction_time TEXT,
                product_family TEXT,
                spec_completeness TEXT,
                is_urgent_replacement TEXT,
                contract_due_date TEXT,
                workflow_state TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO clean_enquiries VALUES
            ('SN-S3-01', 'ACCT-S3-01', 'SEALED_LATER_TEST', 'Email', ?, ?, 'Motor_DongCo', 'Full', 'No', '', 'New'),
            ('SN-S3-02', 'ACCT-S3-02', 'SEALED_LATER_TEST', 'WebForm', ?, ?, 'Drive_BienTan', 'Partial', 'Yes', '', 'New')
            """,
            (now_iso, now_iso, now_iso, now_iso),
        )
        conn.execute(
            """
            CREATE TABLE raw_outcomes (
                enquiry_id TEXT PRIMARY KEY,
                label TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO raw_outcomes VALUES
            ('SN-S3-01', 'Unknown'),
            ('SN-S3-02', 'POSITIVE')
            """
        )
        conn.commit()


def test_sprint3_browser_console_end_to_end_and_security_guards(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "sprint3_ui.db"
    q_dir = tmp_path / "quarantine"
    _seed_minimal_db(db_path)

    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(q_dir))
    monkeypatch.setenv("ENV_MODE", "development")
    monkeypatch.setenv("SESSION_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    DEFAULT_SESSION_STORE._sessions.clear()

    client = TestClient(app)

    # 0) Frontend gate_g1.js & HTML tab phục vụ chuẩn
    r_js = client.get("/gate_g1.js")
    assert r_js.status_code == 200 and "loadGateG1Console" in r_js.text
    r_html = client.get("/")
    assert r_html.status_code == 200
    assert "tab-btn-gate-g1" in r_html.text and "gate-g1-panel" in r_html.text

    # 1) Bước 1.2: Direct URL & Expired session phải trả HTTP 401
    assert client.get("/api/v1/workspace/queue").status_code == 401
    assert (
        client.get(
            "/api/v1/workspace/queue",
            headers={"X-User-Role": "SALES_READER", "X-Session-Expired": "true"},
        ).status_code
        == 401
    )

    # 2) RBAC: SALES_READER bị chặn 403 trên Gate G1 Console & Sprint 3 DEV Sandbox
    sales_hdr = _login(client, "sales_s3", "SALES_READER")
    sup_hdr = _login(client, "sup_s3", "SUPERVISOR")
    reviewer_hdr = _login(client, "rev_s3", "MODEL_REVIEWER")

    assert client.get("/api/v1/gate-g1/evidence", headers=sales_hdr).status_code == 403
    assert (
        client.post(
            "/api/v1/dev-sandbox/run-sprint3-check",
            headers=sales_hdr,
            json={"suite_id": "plpt28_single_use_verify"},
        ).status_code
        == 403
    )

    # 3) Bước 2 (PLPT-25): Ghi ACCEPT & OVERRIDE, xác minh cô lập tuyệt đối với raw_outcomes
    r_acc = client.post(
        "/api/v1/workspace/review",
        headers=sup_hdr,
        json={
            "enquiry_id": "SN-S3-01",
            "decision_type": "ACCEPT_PRIORITY",
            "contact_permission_verified": True,
            "contact_attempted": True,
            "review_completed": True,
        },
    )
    assert r_acc.status_code == 200

    r_ov = client.post(
        "/api/v1/workspace/override",
        headers=sup_hdr,
        json={
            "enquiry_id": "SN-S3-02",
            "override_reason_code": "CUSTOMER_URGENT_CALLBACK",
            "justification": "Khách hàng gọi gấp kiểm chứng Sprint 3 UI",
        },
    )
    assert r_ov.status_code == 200

    # 4) Bước 2.6 + 3.2 + 4 + 5.1: Đối chiếu bằng chứng qua /api/v1/gate-g1/evidence
    r_ev = client.get("/api/v1/gate-g1/evidence", headers=reviewer_hdr)
    assert r_ev.status_code == 200
    ev = r_ev.json()

    assert ev["dev_sandbox_enabled"] is True
    assert ev["workspace_isolation"]["staff_review_logs_count"] == 1
    assert ev["workspace_isolation"]["staff_override_logs_count"] == 1
    assert ev["workspace_isolation"]["raw_outcomes_count"] == 2
    assert ev["workspace_isolation"]["raw_outcomes_unknown_count"] == 1

    assert ev["manifest"]["exists"] is True
    assert ev["manifest"]["checks"]["all_passed"] is True
    assert ev["manifest"]["data"]["serving_mode_locked"] == "RULES_BASELINE"

    assert ev["control_fixtures"]["all_40_passed"] is True
    assert ev["control_fixtures"]["data"]["passed_count"] == 40
    assert ev["control_fixtures"]["data"]["failed_count"] == 0

    assert ev["gate_g1_benchmark"]["rules_fallback_locked"] is True
    assert ev["gate_g1_benchmark"]["data"]["gate_g1_decision"] == "FALLBACK_RULES_BASELINE"
    assert ev["gate_g1_benchmark"]["data"]["active_serving_mode"] == "RULES_BASELINE"
    assert ev["sealed_cohort_locks"]["lock_count"] >= 1

    # 5) Bước 5.2: Allowlist validation + Single-Use Sealed Cohort lock verification
    r_bad = client.post(
        "/api/v1/dev-sandbox/run-sprint3-check",
        headers=reviewer_hdr,
        json={"suite_id": "arbitrary_cmd"},
    )
    assert r_bad.status_code == 400

    lock_path = PROJECT_ROOT / "artifacts" / "gate_g1" / "sealed_cohort_locks.json"
    orig_lock_bytes = lock_path.read_bytes()

    r_lock = client.post(
        "/api/v1/dev-sandbox/run-sprint3-check",
        headers=reviewer_hdr,
        json={"suite_id": "plpt28_single_use_verify"},
    )
    assert r_lock.status_code == 200
    lock_body = r_lock.json()
    assert lock_body["passed"] is True
    assert lock_body["single_use_lock_verified"] is True
    assert lock_path.read_bytes() == orig_lock_bytes

    # 6) SEC-06: Ở ENV_MODE=production, DEV sandbox bị khóa 403 nhưng read-only evidence vẫn hoạt động
    monkeypatch.setenv("ENV_MODE", "production")
    r_prod_ev = client.get("/api/v1/gate-g1/evidence", headers=reviewer_hdr)
    assert r_prod_ev.status_code == 200
    assert r_prod_ev.json()["dev_sandbox_enabled"] is False
    assert (
        client.post(
            "/api/v1/dev-sandbox/run-sprint3-check",
            headers=reviewer_hdr,
            json={"suite_id": "plpt28_single_use_verify"},
        ).status_code
        == 403
    )
