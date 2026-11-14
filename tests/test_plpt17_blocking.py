"""Unit tests cho PLPT-17 (US-09): Emergency Block & 24h Stale Data Hold Controls."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app

VN_TZ = timezone(timedelta(hours=7))


def _create_test_db(db_path: Path, extraction_time: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE clean_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT NOT NULL,
                t0_time TEXT NOT NULL,
                extraction_time TEXT NOT NULL,
                product_family TEXT NOT NULL,
                spec_completeness TEXT,
                is_urgent_replacement TEXT,
                contract_due_date TEXT,
                workflow_state TEXT,
                enquiry_channel_at_t0 TEXT,
                account_age_days_at_t0 INTEGER,
                prior_purchase_count_at_t0 INTEGER,
                estimated_value_band_at_t0 TEXT
            )
            """
        )
        rows = [
            (
                "SN-0001",
                "ACCT-0001",
                "2026-11-10T08:00:00+07:00",
                extraction_time,
                "Motor_DongCo",
                "Full",
                "Urgent",
                "2026-11-15",
                "New",
                "Direct_Sales",
                400,
                5,
                "High",
            ),
            (
                "SN-0002",
                "ACCT-0002",
                "2026-11-10T09:00:00+07:00",
                extraction_time,
                "Drive_BienTan",
                "Full",
                "Normal",
                "2026-12-20",
                "New",
                "Email",
                120,
                2,
                "Medium",
            ),
            (
                "SN-0003",
                "ACCT-0003",
                "2026-11-10T10:00:00+07:00",
                extraction_time,
                "Transmission_HopSo",
                "Partial",
                "Normal",
                "2026-12-25",
                "New",
                "Website",
                30,
                0,
                "Low",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO clean_enquiries (
                enquiry_id, account_key, t0_time, extraction_time, product_family,
                spec_completeness, is_urgent_replacement, contract_due_date,
                workflow_state, enquiry_channel_at_t0, account_age_days_at_t0,
                prior_purchase_count_at_t0, estimated_value_band_at_t0
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def test_emergency_block_removes_record_and_reindexes_rank(tmp_path, monkeypatch):
    db_path = tmp_path / "test_plpt17.db"
    fresh_ext = (datetime.now(VN_TZ) - timedelta(hours=2)).replace(microsecond=0).isoformat()
    _create_test_db(db_path, fresh_ext)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    client = TestClient(app)

    before = client.get("/api/queue?limit=10").json()
    assert before["status"] == "OK"
    assert before["total"] == 3
    assert before["items"][0]["enquiry_id"] == "SN-0001"
    assert before["items"][0]["final_rank"] == 1

    block_res = client.post(
        "/api/block",
        json={
            "enquiry_id": "SN-0001",
            "account_key": "ACCT-0001",
            "reason": "PO emergency block test",
        },
    )
    assert block_res.status_code == 200
    assert block_res.json()["blocked"] is True

    after = client.get("/api/queue?limit=10").json()
    assert after["status"] == "OK"
    assert after["total"] == 2
    assert all(item["enquiry_id"] != "SN-0001" for item in after["items"])
    assert after["items"][0]["enquiry_id"] == "SN-0002"
    assert after["items"][0]["final_rank"] == 1


def test_stale_data_locks_queue_after_24_hours(tmp_path, monkeypatch):
    db_path = tmp_path / "test_stale.db"
    stale_ext = (datetime.now(VN_TZ) - timedelta(hours=30)).replace(microsecond=0).isoformat()
    _create_test_db(db_path, stale_ext)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    client = TestClient(app)
    res = client.get("/api/queue?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "HELD_STALE_DATA"
    assert data["locked"] is True
    assert data["source_age_hours"] > 24.0
    assert data["items"] == []
    assert data["data"] == []


def test_fresh_data_preserves_total_and_backward_compatible_keys(tmp_path, monkeypatch):
    db_path = tmp_path / "test_fresh.db"
    fresh_ext = (datetime.now(VN_TZ) - timedelta(hours=1)).replace(microsecond=0).isoformat()
    _create_test_db(db_path, fresh_ext)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    client = TestClient(app)
    res = client.get("/api/queue?limit=1")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "OK"
    assert data["locked"] is False
    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["items"] == data["data"]