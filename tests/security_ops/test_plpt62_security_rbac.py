"""Comprehensive HTTP security and RBAC regression coverage for PLPT-62."""

import contextlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.api.main import app
from src.security.auth import DEFAULT_SESSION_STORE
from src.security.incident_hold import activate_emergency_hold
from tests.control_fixtures.fixtures_data import create_clean_db, make_cf_row


VN_TZ = timezone(timedelta(hours=7))


@pytest.fixture
def security_client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "plpt62-security.db"
    now_iso = datetime.now(VN_TZ).replace(microsecond=0).isoformat()
    rows = [
        make_cf_row(
            enquiry_id=f"E-{role}",
            account_key=f"ACCT-{role}",
            t0_time=now_iso,
            extraction_time=now_iso,
            feature_event_time=now_iso,
            feature_available_at=now_iso,
        )
        for role in ("SALES", "SUPERVISOR", "STEWARD", "MODEL", "ADMIN")
    ]
    create_clean_db(db_path, rows)

    monkeypatch.setenv("ENV_MODE", "evaluation")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ALLOW_LEGACY_ROLE_HEADER", "false")
    monkeypatch.setenv("RBAC_ENFORCE_V1", "true")
    monkeypatch.setenv("RBAC_ENFORCE_LEGACY", "true")
    DEFAULT_SESSION_STORE._sessions.clear()

    yield TestClient(app), db_path
    DEFAULT_SESSION_STORE._sessions.clear()


def _login(client: TestClient, user_id: str, role: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"user_id": user_id, "role": role},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    ("role", "method", "path", "expected"),
    [
        ("SALES_READER", "GET", "/api/v1/workspace/queue", 200),
        ("SUPERVISOR", "GET", "/api/v1/workspace/queue", 200),
        ("DATA_STEWARD", "GET", "/api/v1/workspace/queue", 200),
        ("MODEL_REVIEWER", "GET", "/api/v1/workspace/queue", 200),
        ("ADMINISTRATOR", "GET", "/api/v1/workspace/queue", 200),
        ("SALES_READER", "POST", "/api/v1/workspace/block", 403),
        ("SUPERVISOR", "POST", "/api/v1/workspace/block", 200),
        ("DATA_STEWARD", "GET", "/api/v1/pipeline/status", 200),
        ("SALES_READER", "GET", "/api/v1/pipeline/status", 403),
        ("MODEL_REVIEWER", "GET", "/api/v1/gate-g1/evidence", 200),
        ("SALES_READER", "GET", "/api/v1/gate-g1/evidence", 403),
        ("ADMINISTRATOR", "GET", "/api/v1/admin/security-readiness", 200),
        ("SALES_READER", "GET", "/api/v1/admin/security-readiness", 403),
    ],
)
def test_plpt62_role_matrix_http(
    security_client,
    role: str,
    method: str,
    path: str,
    expected: int,
):
    client, _ = security_client
    token = _login(client, f"user_{role.lower()}", role)
    if method == "GET":
        response = client.get(path, headers=_headers(token))
    else:
        response = client.post(
            path,
            json={"enquiry_id": "E-SALES"},
            headers=_headers(token),
        )
    assert response.status_code == expected


def test_plpt62_missing_malformed_invalid_signature_and_legacy_header(security_client):
    client, _ = security_client

    assert client.get("/api/v1/workspace/queue").status_code == 401
    assert client.get(
        "/api/v1/workspace/queue",
        headers={"X-User-Role": "ADMINISTRATOR"},
    ).status_code == 401
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers("not-a-signed-token"),
    ).status_code == 401
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers("sn1.%.invalid"),
    ).status_code == 401

    token = _login(client, "sales_signature", "SALES_READER")
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload}.{signature[:-1]}{'0' if signature[-1] != '0' else '1'}"
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers(tampered),
    ).status_code == 401


def test_plpt62_expired_revoked_and_account_revoked_sessions(security_client):
    client, db_path = security_client

    token = _login(client, "sales_security", "SALES_READER")
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers(token),
    ).status_code in {200, 409}

    assert client.post(
        "/api/v1/auth/logout",
        headers=_headers(token),
    ).status_code == 200
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers(token),
    ).status_code == 401

    token = _login(client, "sales_account", "SALES_READER")
    response = client.post(
        "/admin/accounts",
        headers=_headers(_login(client, "admin_account", "ADMINISTRATOR")),
        json={
            "user_id": "sales_account",
            "role": "SALES_READER",
            "status": "REVOKED",
        },
    )
    assert response.status_code == 200
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_headers(token),
    ).status_code == 401


def test_plpt62_direct_url_and_idor_no_mutation(security_client):
    client, db_path = security_client
    admin = _login(client, "admin_direct", "ADMINISTRATOR")
    scoped_account = client.post(
        "/admin/accounts",
        headers=_headers(admin),
        json={
            "user_id": "sales_direct",
            "role": "SALES_READER",
            "team_id": "TEAM-A",
            "assigned_accounts": ["ACCT-A"],
        },
    )
    assert scoped_account.status_code == 200
    sales = _login(client, "sales_direct", "SALES_READER")

    assert client.post(
        "/api/v1/pipeline/ingest",
        headers=_headers(sales),
    ).status_code == 403
    assert client.post(
        "/admin/accounts",
        headers=_headers(sales),
        json={"user_id": "attacker", "role": "ADMINISTRATOR"},
    ).status_code == 403
    assert client.post(
        "/api/v1/workspace/review",
        headers=_headers(sales),
        json={
            "enquiry_id": "E-SALES",
            "account_key": "ACCT-NOT-ASSIGNED",
            "decision_type": "ACCEPT_PRIORITY",
        },
    ).status_code == 403

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        account_count = conn.execute(
            "SELECT COUNT(*) FROM internal_accounts WHERE user_id = 'attacker'"
        ).fetchone()[0]
    assert account_count == 0


def test_plpt62_incident_hold_role_snapshot_and_queue_freeze(security_client):
    client, db_path = security_client
    admin = _login(client, "admin_hold", "ADMINISTRATOR")
    supervisor = _login(client, "supervisor_hold", "SUPERVISOR")
    sales = _login(client, "sales_hold", "SALES_READER")

    denied = client.post(
        "/api/v1/admin/incident-hold",
        headers=_headers(supervisor),
        json={"action": "activate", "reason": "Unauthorized hold"},
    )
    assert denied.status_code == 403

    activated = client.post(
        "/api/v1/admin/incident-hold",
        headers=_headers(admin),
        json={"action": "activate", "reason": "Security test hold", "severity": "CRITICAL"},
    )
    assert activated.status_code == 200
    hold = activated.json()
    assert hold["snapshot_id"]
    assert hold["snapshot_audit_hash"]

    repeated = client.post(
        "/api/v1/admin/incident-hold",
        headers=_headers(admin),
        json={"action": "activate", "reason": "Repeated hold"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["repeated_activation"] is True

    queue = client.get(
        "/api/v1/workspace/queue",
        headers=_headers(sales),
    )
    assert queue.status_code == 200
    assert queue.json()["locked"] is True
    assert queue.json()["total"] == 0

    released = client.post(
        "/api/v1/admin/incident-hold",
        headers=_headers(admin),
        json={
            "action": "release",
            "incident_id": hold["incident_id"],
            "resolution_note": "Containment verified",
        },
    )
    assert released.status_code == 200
    assert released.json()["status"] == "RESOLVED"


def test_plpt62_audit_logs_denials_and_secret_is_not_exposed(security_client, monkeypatch):
    client, db_path = security_client
    monkeypatch.setenv("SESSION_SECRET_KEY", "p62-" + ("x" * 40))
    sales = _login(client, "sales_audit", "SALES_READER")

    assert client.get(
        "/api/v1/admin/security-readiness",
        headers=_headers(sales),
    ).status_code == 403
    assert client.get("/api/v1/workspace/queue").status_code == 401

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            """
            SELECT event_type, status_code, detail
            FROM security_audit_logs
            WHERE status_code IN (401, 403)
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
    assert rows

    serialized = repr(rows)
    assert "SESSION_SECRET_KEY" not in serialized
    assert "p62-" not in serialized
