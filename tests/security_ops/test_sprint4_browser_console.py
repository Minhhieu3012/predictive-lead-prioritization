"""Tests cho Sprint 4 Browser Console (PLPT-55..PLPT-58, SQLite Audit/Isolation, Drift Sandbox & SEC-06)."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import app
from src.security.auth import DEFAULT_SESSION_STORE


def _login(client: TestClient, user_id: str, role: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"user_id": user_id, "role": role})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_sprint4_browser_console_end_to_end_and_security_guards(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "sprint4_ui.db"
    q_dir = tmp_path / "quarantine"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(q_dir))
    monkeypatch.setenv("ENV_MODE", "development")
    monkeypatch.setenv("SESSION_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    DEFAULT_SESSION_STORE._sessions.clear()

    client = TestClient(app)

    # 0) Frontend sprint4.js & HTML tab phục vụ chuẩn
    r_js = client.get("/sprint4.js")
    assert r_js.status_code == 200 and "loadSprint4Console" in r_js.text
    r_html = client.get("/")
    assert r_html.status_code == 200
    assert "tab-btn-sprint4" in r_html.text and "sprint4-panel" in r_html.text

    # 1) RBAC: SALES_READER bị chặn 403 trên Sprint 4 console & DEV sandbox
    sales_hdr = _login(client, "sales_s4", "SALES_READER")
    admin_hdr = _login(client, "admin_s4", "ADMINISTRATOR")

    assert client.get("/api/v1/sprint4/overview", headers=sales_hdr).status_code == 403
    assert client.post("/api/v1/dev-sandbox/simulate-drift", headers=sales_hdr, json={}).status_code == 403
    assert client.post("/api/v1/dev-sandbox/restore-clean-drift", headers=sales_hdr).status_code == 403
    assert (
        client.post(
            "/api/v1/dev-sandbox/run-sprint4-check",
            headers=sales_hdr,
            json={"suite_id": "plpt55_rbac_incident"},
        ).status_code
        == 403
    )

    # Nạp dữ liệu sạch vào DB tạm cho các bước kiểm thử Sprint 4
    assert client.post("/api/v1/pipeline/ingest", headers=admin_hdr).status_code == 200
    assert client.post("/api/v1/pipeline/deduplicate", headers=admin_hdr).status_code == 200

    # 2) Bước 1.2 (PLPT-55): Login -> /api/block (403) -> Logout -> Reuse token (401)
    assert client.post("/api/block", headers=sales_hdr, json={"enquiry_id": "SN-0001"}).status_code == 403
    r_out = client.post("/api/v1/auth/logout", headers=sales_hdr)
    assert r_out.status_code == 200 and r_out.json()["revoked"] is True
    assert client.get("/api/v1/workspace/queue", headers=sales_hdr).status_code == 401

    # 3) Bước 1.3 (PLPT-55): Incident Hold 4h activate -> get -> notify -> release & Security Readiness
    r_act = client.post(
        "/api/v1/admin/incident-hold",
        headers=admin_hdr,
        json={"action": "activate", "severity": "CRITICAL", "reason": "Demo Sprint 4 UI"},
    )
    assert r_act.status_code == 200
    inc_id = r_act.json()["incident_id"]

    r_get = client.get("/api/v1/admin/incident-hold", headers=admin_hdr)
    assert r_get.status_code == 200 and r_get.json()["active"] is True

    r_not = client.post(
        "/api/v1/admin/incident-hold",
        headers=admin_hdr,
        json={"action": "notify", "incident_id": inc_id, "channel": "OFFICIAL_EMAIL"},
    )
    assert r_not.status_code == 200 and r_not.json()["within_4h_sla"] is True

    r_rel = client.post(
        "/api/v1/admin/incident-hold",
        headers=admin_hdr,
        json={"action": "release", "incident_id": inc_id, "resolution_note": "Done demo"},
    )
    assert r_rel.status_code == 200 and r_rel.json()["status"] == "RESOLVED"

    r_ready = client.get("/api/v1/admin/security-readiness", headers=admin_hdr)
    assert r_ready.status_code == 200
    assert len(r_ready.json()["vn_legal_compliance"]["instruments"]) == 6
    assert r_ready.json()["sales_training_status"]["status"] == "COMPLETED_PILOT_BRIEFING"

    # 4) Bước 2.2 (PLPT-56): Shadow Summary & Fallback
    r_sh = client.get("/api/v1/shadow/summary", headers=admin_hdr)
    assert r_sh.status_code == 200
    assert r_sh.json()["serving_route"]["rules_only_enforced"] is True

    r_fb = client.post(
        "/api/v1/shadow/fallback",
        headers=admin_hdr,
        json={"drift_fallback_triggered": True, "control_health_ok": True},
    )
    assert r_fb.status_code == 200
    assert r_fb.json()["active_mode"] == "FALLBACK_RULES_BASELINE"
    assert r_fb.json()["rules_only_enforced"] is True

    # 5) Bước 3.2 (PLPT-57): 2 yêu cầu cùng account_key -> nhất quán treatment_arm (0 xung đột)
    p1 = client.post(
        "/api/v1/pilot/effort",
        headers=admin_hdr,
        json={
            "enquiry_id": "SN-0001",
            "account_key": "ACCT-DEMO-01",
            "enquiries_reviewed": 5,
            "queue_prep_minutes": 3,
            "triage_duration_seconds": 900,
            "due_total": 5,
            "due_on_time": 5,
        },
    )
    p2 = client.post(
        "/api/v1/pilot/effort",
        headers=admin_hdr,
        json={
            "enquiry_id": "SN-0002",
            "account_key": "ACCT-DEMO-01",
            "enquiries_reviewed": 5,
            "queue_prep_minutes": 2,
            "triage_duration_seconds": 840,
            "due_total": 5,
            "due_on_time": 5,
        },
    )
    assert p1.status_code == 200 and p2.status_code == 200
    assert p1.json()["treatment_arm"] == p2.json()["treatment_arm"]

    # 6) Bước 4.3 (PLPT-58): Giả lập drift 200 dòng -> MISSINGNESS_SPIKE & LOW_COVERAGE -> Khôi phục sạch
    r_sim = client.post("/api/v1/dev-sandbox/simulate-drift", headers=admin_hdr, json={"rows": 200})
    assert r_sim.status_code == 200 and r_sim.json()["updated_rows"] == 200

    r_dr_bad = client.get("/api/v1/monitoring/drift", headers=admin_hdr)
    assert r_dr_bad.status_code == 200
    dr_bad = r_dr_bad.json()
    alert_codes = {a["code"] for a in dr_bad["alerts"]}
    assert dr_bad["investigation_triggered"] is True
    assert dr_bad["auto_retrain_enabled"] is False
    assert "MISSINGNESS_SPIKE" in alert_codes and "LOW_COVERAGE" in alert_codes

    r_res = client.post("/api/v1/dev-sandbox/restore-clean-drift", headers=admin_hdr)
    assert r_res.status_code == 200 and r_res.json()["deduplication"]["math_check_passed"] is True

    r_dr_clean = client.get("/api/v1/monitoring/drift", headers=admin_hdr)
    assert r_dr_clean.status_code == 200
    clean_codes = {a["code"] for a in r_dr_clean.json()["alerts"]}
    assert "MISSINGNESS_SPIKE" not in clean_codes
    assert r_dr_clean.json()["auto_retrain_enabled"] is False

    # 7) Kiểm tra GET /api/v1/sprint4/overview tổng hợp đủ bảng SQLite & .env.example
    r_ov = client.get("/api/v1/sprint4/overview", headers=admin_hdr)
    assert r_ov.status_code == 200
    ov = r_ov.json()
    assert ov["dev_sandbox_enabled"] is True
    assert ov["env_example"]["all_required_present"] is True
    assert ov["security"]["revoked_sessions_count"] >= 1
    assert ov["security"]["security_audit_logs_count"] >= 1
    assert ov["security"]["active_incident_hold"] is None
    assert ov["shadow"]["manifest_serving_mode_locked"] == "RULES_BASELINE"
    assert ov["pilot"]["conflicting_accounts"] == 0
    assert ov["pilot"]["pilot_assignments_count"] >= 1
    assert ov["pilot"]["exposure_logs_count"] == 2
    assert ov["drift"]["drift_investigation_alerts_count"] >= 2

    # 8) Allowlist validation & SEC-06 ENV_MODE=production chặn cứng 403
    assert (
        client.post(
            "/api/v1/dev-sandbox/run-sprint4-check",
            headers=admin_hdr,
            json={"suite_id": "invalid_cmd"},
        ).status_code
        == 400
    )

    monkeypatch.setenv("ENV_MODE", "production")
    r_prod_ov = client.get("/api/v1/sprint4/overview", headers=admin_hdr)
    assert r_prod_ov.status_code == 200 and r_prod_ov.json()["dev_sandbox_enabled"] is False
    assert client.post("/api/v1/dev-sandbox/simulate-drift", headers=admin_hdr, json={}).status_code == 403
    assert client.post("/api/v1/dev-sandbox/restore-clean-drift", headers=admin_hdr).status_code == 403
    assert (
        client.post(
            "/api/v1/dev-sandbox/run-sprint4-check",
            headers=admin_hdr,
            json={"suite_id": "plpt55_rbac_incident"},
        ).status_code
        == 403
    )
