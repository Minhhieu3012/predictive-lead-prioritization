"""Tests cho Sprint 2 Browser Console (PLPT-14..PLPT-17, RBAC & ENV_MODE DEV-ONLY guard)."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.main import app
from src.security.auth import DEFAULT_SESSION_STORE


def _login(client: TestClient, user_id: str, role: str) -> dict[str, str]:
    res = client.post("/api/v1/auth/login", json={"user_id": user_id, "role": role})
    assert res.status_code == 200
    return {"Authorization": f"Bearer {res.json()['token']}"}


def test_sprint2_browser_console_end_to_end_and_security_guards(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "sprint2_ui.db"
    q_dir = tmp_path / "quarantine"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(q_dir))
    monkeypatch.setenv("ENV_MODE", "development")
    monkeypatch.setenv("SESSION_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    DEFAULT_SESSION_STORE._sessions.clear()

    client = TestClient(app)

    # 0) Frontend pipeline.js phục vụ chuẩn
    r_js = client.get("/pipeline.js")
    assert r_js.status_code == 200 and "loadPipelineConsole" in r_js.text

    # 1) RBAC: SALES_READER bị chặn 403 trên pipeline & /api/block
    sales_hdr = _login(client, "sales_s2", "SALES_READER")
    admin_hdr = _login(client, "admin_s2", "ADMINISTRATOR")

    assert client.get("/api/v1/pipeline/status", headers=sales_hdr).status_code == 403
    assert client.post("/api/v1/pipeline/ingest", headers=sales_hdr).status_code == 403
    assert client.post("/api/block", headers=sales_hdr, json={"enquiry_id": "SN-0001"}).status_code == 403

    # 2) PLPT-14: Sinh dữ liệu mẫu (DEV) + Audit US-03 + Nạp CSV 2 lần (Idempotent)
    r_gen = client.post("/api/v1/dev-sandbox/generate-synthetic", headers=admin_hdr)
    assert r_gen.status_code == 200
    assert r_gen.json()["enquiries_rows"] == 1260

    r_audit = client.get("/api/v1/pipeline/us03-audit", headers=admin_hdr)
    assert r_audit.status_code == 200
    audit_body = r_audit.json()
    assert audit_body["pass_count"] == 10
    assert audit_body["fail_count"] == 0
    assert audit_body["quarantine_count"] == 10

    r_ing1 = client.post("/api/v1/pipeline/ingest", headers=admin_hdr)
    assert r_ing1.status_code == 200
    assert r_ing1.json()["after_raw_enquiries"] == 1250
    assert r_ing1.json()["do_not_use_columns_present"] == []
    assert r_ing1.json()["quarantine_enquiries"] == 10

    r_ing2 = client.post("/api/v1/pipeline/ingest", headers=admin_hdr)
    assert r_ing2.status_code == 200
    assert r_ing2.json()["delta_raw_enquiries"] == 0
    assert r_ing2.json()["idempotent_no_duplicates"] is True

    # 3) PLPT-15: Gộp trùng đa kênh -> raw - clean == merged
    r_dedup = client.post("/api/v1/pipeline/deduplicate", headers=admin_hdr)
    assert r_dedup.status_code == 200
    dedup_body = r_dedup.json()
    assert dedup_body["math_check_passed"] is True
    assert dedup_body["merged_count"] > 0

    r_status = client.get("/api/v1/pipeline/status", headers=admin_hdr)
    assert r_status.status_code == 200
    st = r_status.json()
    assert st["raw_enquiries"]["pii_and_leakage_stripped"] is True
    assert st["raw_outcomes"]["unknown_count"] == 50
    assert st["deduplication"]["math_check_ok"] is True
    assert st["freshness"]["status"] == "OK"

    # 4) PLPT-17: Giả lập dữ liệu cũ > 24h -> HOLD_STALE_DATA -> Khôi phục dữ liệu tươi
    r_stale = client.post("/api/v1/dev-sandbox/simulate-stale", headers=admin_hdr, json={})
    assert r_stale.status_code == 200
    q_stale = client.get("/api/queue", headers=sales_hdr).json()
    assert q_stale["status"] == "HOLD_STALE_DATA"
    assert q_stale["locked"] is True
    assert q_stale["items"] == []

    r_fresh = client.post("/api/v1/dev-sandbox/restore-fresh", headers=admin_hdr)
    assert r_fresh.status_code == 200
    q_ok = client.get("/api/queue?limit=5", headers=sales_hdr).json()
    assert q_ok["status"] == "OK"
    assert q_ok["locked"] is False
    assert len(q_ok["items"]) == 5

    # 5) SEC-06: Ở ENV_MODE=production, mọi endpoint /api/v1/dev-sandbox/* BẮT BUỘC bị khóa 403
    monkeypatch.setenv("ENV_MODE", "production")
    assert client.post("/api/v1/dev-sandbox/generate-synthetic", headers=admin_hdr).status_code == 403
    assert client.post("/api/v1/dev-sandbox/simulate-stale", headers=admin_hdr, json={}).status_code == 403
    assert client.post("/api/v1/dev-sandbox/restore-fresh", headers=admin_hdr).status_code == 403
    assert client.post("/api/v1/dev-sandbox/run-sprint2-check", headers=admin_hdr, json={"suite_id": "plpt14_sync"}).status_code == 403
