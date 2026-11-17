from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient

from src.api.main import app

VN_TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=VN_TZ)


def _create_workspace_db(db_path, extraction_time):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE clean_enquiries (
                enquiry_id TEXT PRIMARY KEY,
                account_key TEXT,
                cohort TEXT,
                t0_time TEXT,
                extraction_time TEXT,
                product_family TEXT,
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
        conn.executemany(
            """
            INSERT INTO clean_enquiries (
                enquiry_id, account_key, cohort, t0_time, extraction_time,
                product_family, spec_completeness, is_urgent_replacement,
                contract_due_date, workflow_state, enquiry_channel_at_t0,
                account_age_days_at_t0, prior_purchase_count_at_t0,
                estimated_value_band_at_t0
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"SN-{index:03d}",
                    f"ACCT-{index:03d}",
                    "C1" if index % 2 else "C2",
                    NOW.isoformat(),
                    extraction_time,
                    "Motor_DongCo",
                    "Full",
                    "Normal",
                    "2027-01-01",
                    "New",
                    "Email",
                    30,
                    0,
                    "Medium",
                )
                for index in range(1, 21)
            ],
        )


def test_daily_queue_api_persists_idempotent_allocation(tmp_path, monkeypatch):
    db_path = tmp_path / "workspace.db"
    fresh_extraction = (
        datetime.now(VN_TZ) - timedelta(hours=1)
    ).replace(microsecond=0).isoformat()
    _create_workspace_db(db_path, fresh_extraction)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("REVIEW_CAPACITY_K", "10")

    client = TestClient(app)
    first = client.post("/api/v1/workspace/queue/daily?seed=44")
    repeated = client.post("/api/v1/workspace/queue/daily?seed=99")

    assert first.status_code == 200
    assert repeated.status_code == 200
    first_allocation = first.json()["allocation"]
    repeated_allocation = repeated.json()["allocation"]
    assert first_allocation["capacity_k"] == 10
    assert first_allocation["exploration_seed"] == 44
    assert first_allocation["selected"] == repeated_allocation["selected"]
    assert repeated_allocation["exploration_seed"] == 44
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_queue_allocations"
        ).fetchone()[0] == 1


def test_workspace_queue_api_separates_views_and_rank_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "workspace_views.db"
    fresh_extraction = (
        datetime.now(VN_TZ) - timedelta(hours=1)
    ).replace(microsecond=0).isoformat()
    _create_workspace_db(db_path, fresh_extraction)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE clean_enquiries SET is_urgent_replacement = 'Urgent' "
            "WHERE enquiry_id = 'SN-020'"
        )

    quarantine_path = tmp_path / "quarantine_rows.csv"
    quarantine_path.write_text(
        "enquiry_id,account_key,cohort,source,t0_time,extraction_time,"
        "product_family,quarantine_reason,contact_phone_DO_NOT_USE\n"
        "ERR-1,ACCT-X,C1,CSV,,2026-11-17T14:00:00+07:00,"
        "Unknown,Missing t0,0900000000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("REVIEW_CAPACITY_K", "10")
    monkeypatch.setattr("src.api.main.QUARANTINE_PATH", quarantine_path)

    client = TestClient(app)
    created = client.post("/api/v1/workspace/queue/daily?seed=44")
    active = client.get("/api/v1/workspace/queue?view=Active")

    assert created.status_code == 200
    assert active.status_code == 200
    active_payload = active.json()
    assert active_payload["view"] == "Active"
    assert active_payload["snapshot_age_unit"] == "seconds"
    assert active_payload["capacity_k"] == 10
    assert active_payload["scheduled_today"] == 10
    first = active_payload["items"][0]
    assert first["enquiry_id"] == "SN-020"
    assert first["model_rank"] == 10
    assert first["final_work_order"] == 1
    assert first["cohort_id"] == "C2"
    assert first["owner"] is None
    assert first["score_mode"] == "RULES_BASELINE"
    assert len(first["contributing_factors"]) <= 3
    assert first["contributing_factors"][0]["source_timestamp"] == NOW.isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO clean_enquiries
            SELECT 'SN-021', 'ACCT-021', cohort, t0_time, extraction_time,
                   product_family, spec_completeness, is_urgent_replacement,
                   contract_due_date, workflow_state, enquiry_channel_at_t0,
                   account_age_days_at_t0, prior_purchase_count_at_t0,
                   estimated_value_band_at_t0
            FROM clean_enquiries
            WHERE enquiry_id = 'SN-001'
            """
        )

    unscored = client.get("/api/v1/workspace/queue?view=Unscored").json()
    assert unscored["view"] == "Unscored"
    assert unscored["classification_basis"] == (
        "Not present in the persisted daily scoring snapshot"
    )
    assert [item["enquiry_id"] for item in unscored["items"]] == ["SN-021"]
    assert unscored["items"][0]["model_rank"] is None
    assert unscored["items"][0]["contributing_factors"] == []

    quarantine = client.get(
        "/api/v1/workspace/queue?view=Quarantine"
    ).json()
    assert quarantine["view"] == "Quarantine"
    assert quarantine["total"] == 1
    assert quarantine["items"][0]["quarantine_reason"] == "Missing t0"
    assert "contact_phone_DO_NOT_USE" not in quarantine["items"][0]
