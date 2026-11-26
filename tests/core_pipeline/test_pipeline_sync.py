"""Integration & edge-case tests cho PLPT-14, PLPT-15, PLPT-16, PLPT-17."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import contextlib
import sqlite3

from fastapi.testclient import TestClient

def _auth_headers(client: TestClient, role: str, user_id: str | None = None, *, expired: bool = False) -> dict[str, str]:
    from src.security.auth import DEFAULT_SESSION_STORE
    DEFAULT_SESSION_STORE._sessions.clear()
    uid = user_id or f"test_{role.lower()}"
    res = client.post("/auth/login", json={"user_id": uid, "role": role})
    assert res.status_code == 200, res.text
    hdrs = {"Authorization": f"Bearer {res.json()['token']}"}
    if expired:
        hdrs["X-Session-Expired"] = "true"
    return hdrs

import pandas as pd

from src.api.main import app
from src.ingest import csv_validator, deduplicator

VN_TZ = timezone(timedelta(hours=7))


def _write_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


def _configure_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path, Path, Path]:
    enq_csv = tmp_path / "enquiries_snapshot.csv"
    out_csv = tmp_path / "outcome_events.csv"
    q_enq = tmp_path / "quarantine_rows.csv"
    q_out = tmp_path / "quarantine_outcomes.csv"
    db_path = tmp_path / "songnhat.db"

    monkeypatch.setattr(csv_validator, "ENQUIRIES_PATH", enq_csv)
    monkeypatch.setattr(csv_validator, "OUTCOMES_PATH", out_csv)
    monkeypatch.setattr(csv_validator, "QUARANTINE_PATH", q_enq)
    monkeypatch.setattr(csv_validator, "QUARANTINE_OUTCOMES_PATH", q_out)
    monkeypatch.setattr(csv_validator, "DB_PATH", db_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    return enq_csv, out_csv, q_enq, q_out, db_path


def test_upsert_new_to_closed_and_block_older_snapshot(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)

    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext_old = (now - timedelta(hours=5)).isoformat()
    ext_new = (now - timedelta(hours=1)).isoformat()
    ext_older = (now - timedelta(hours=10)).isoformat()

    base_out = [{
        "enquiry_id": "SN-1001",
        "event_type": "Observation_Ended",
        "event_time": ext_old,
        "recorded_at": ext_old,
        "maturity_state": "MATURE",
        "label": "POSITIVE",
        "observation_window_days": 60,
        "cohort": "TRAINING_CANDIDATE",
    }]

    # Lần 1: Nạp New với ext_old
    _write_csv(enq_csv, [{
        "enquiry_id": "SN-1001",
        "account_key": "ACCT-1",
        "t0_time": (now - timedelta(days=2)).isoformat(),
        "extraction_time": ext_old,
        "product_family": "Motor_DongCo",
        "project_ref_code": "PRJ-01",
        "spec_completeness": "Full",
        "workflow_state": "New",
    }])
    _write_csv(out_csv, base_out)
    csv_validator.validate_and_load()

    # Lần 2: Nạp Closed với ext_new (mới hơn -> phải ghi đè)
    _write_csv(enq_csv, [{
        "enquiry_id": "SN-1001",
        "account_key": "ACCT-1",
        "t0_time": (now - timedelta(days=2)).isoformat(),
        "extraction_time": ext_new,
        "product_family": "Motor_DongCo",
        "project_ref_code": "PRJ-01",
        "spec_completeness": "Full",
        "workflow_state": "Closed",
    }])
    csv_validator.validate_and_load()

    # Lần 3: Nạp lại New với ext_older (cũ hơn -> bị chặn không cho ghi đè)
    _write_csv(enq_csv, [{
        "enquiry_id": "SN-1001",
        "account_key": "ACCT-1",
        "t0_time": (now - timedelta(days=2)).isoformat(),
        "extraction_time": ext_older,
        "product_family": "Motor_DongCo",
        "project_ref_code": "PRJ-01",
        "spec_completeness": "Full",
        "workflow_state": "New",
    }])
    csv_validator.validate_and_load()

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        with conn:
            row = conn.execute(
                "SELECT workflow_state, extraction_time, project_ref_code FROM raw_enquiries WHERE enquiry_id='SN-1001'"
            ).fetchone()
    assert row == ("Closed", ext_new, "PRJ-01")

    deduplicator.deduplicate_enquiries(db_path)
    client = TestClient(app)
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert res["total"] == 0
    assert res["items"] == []


def test_withdrawn_excluded_from_queue_and_quarantine_count(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext = (now - timedelta(hours=1)).isoformat()

    _write_csv(enq_csv, [
        {
            "enquiry_id": "SN-OPEN",
            "account_key": "ACCT-1",
            "t0_time": (now - timedelta(days=1)).isoformat(),
            "extraction_time": ext,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
        {
            "enquiry_id": "SN-WD",
            "account_key": "ACCT-2",
            "t0_time": (now - timedelta(days=1)).isoformat(),
            "extraction_time": ext,
            "product_family": "Drive_BienTan",
            "spec_completeness": "Full",
            "workflow_state": "withdrawn",
        },
    ])
    _write_csv(out_csv, [
        {
            "enquiry_id": "SN-OPEN",
            "event_type": "Observation_Ended",
            "event_time": ext,
            "maturity_state": "MATURE",
            "label": "POSITIVE",
            "observation_window_days": 60,
            "cohort": "TRAINING_CANDIDATE",
        },
        {
            "enquiry_id": "SN-BAD-LABEL",
            "event_type": "Observation_Ended",
            "event_time": ext,
            "maturity_state": "INVALID_MAT",
            "label": "Positive",
            "observation_window_days": -5,
            "cohort": "TRAINING_CANDIDATE",
        },
    ])
    csv_validator.validate_and_load()
    deduplicator.deduplicate_enquiries(db_path)

    client = TestClient(app)
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert res["status"] == "OK"
    assert res["total"] == 1
    assert [i["enquiry_id"] for i in res["items"]] == ["SN-OPEN"]
    assert res["quarantine_count"] == 1

    q_out_df = pd.read_csv(tmp_path / "quarantine_outcomes.csv")
    assert len(q_out_df) == 1
    reason = str(q_out_df.loc[0, "quarantine_reason"])
    assert "Sai danh muc label" in reason
    assert "Sai maturity_state" in reason
    assert "observation_window_days am" in reason


def test_default_limit_is_25_and_quarantine_count(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext = (now - timedelta(hours=1)).isoformat()

    enq_rows = []
    out_rows = []
    for i in range(1, 31):
        enq_rows.append({
            "enquiry_id": f"SN-LIM-{i:02d}",
            "account_key": f"ACCT-LIM-{i:02d}",
            "t0_time": (now - timedelta(days=1)).isoformat(),
            "extraction_time": ext,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        })
        out_rows.append({
            "enquiry_id": f"SN-LIM-{i:02d}",
            "event_type": "Observation_Ended",
            "event_time": ext,
            "maturity_state": "MATURE",
            "label": "POSITIVE",
            "observation_window_days": 60,
            "cohort": "TRAINING_CANDIDATE",
        })

    # Thêm 1 dòng lỗi enquiries + 1 dòng lỗi outcomes để kiểm tra quarantine_count = 2
    enq_rows.append({
        "enquiry_id": "ERR-LIM-01",
        "account_key": "",
        "t0_time": ext,
        "extraction_time": ext,
        "product_family": "Motor_DongCo",
        "spec_completeness": "Full",
        "workflow_state": "New",
    })
    out_rows.append({
        "enquiry_id": "SN-LIM-01",
        "event_type": "Observation_Ended",
        "event_time": ext,
        "maturity_state": "MATURE",
        "label": "INVALID_LABEL",
        "observation_window_days": 60,
        "cohort": "TRAINING_CANDIDATE",
    })

    _write_csv(enq_csv, enq_rows)
    _write_csv(out_csv, out_rows)
    csv_validator.validate_and_load()
    deduplicator.deduplicate_enquiries(db_path)

    client = TestClient(app)
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert res["status"] == "OK"
    assert res["total"] == 30
    assert len(res["items"]) == 25
    assert len(res["data"]) == 25
    assert res["quarantine_count"] == 2


def test_closed_does_not_swallow_new_open_and_60d_window(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext_old = (now - timedelta(hours=3)).isoformat()
    ext_new = (now - timedelta(hours=1)).isoformat()

    _write_csv(enq_csv, [
        {
            "enquiry_id": "SN-OLD-CLOSED",
            "account_key": "ACCT-99",
            "t0_time": (now - timedelta(days=20)).isoformat(),
            "extraction_time": ext_old,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "Closed",
        },
        {
            "enquiry_id": "SN-NEW-OPEN",
            "account_key": "ACCT-99",
            "t0_time": (now - timedelta(days=1)).isoformat(),
            "extraction_time": ext_new,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
        {
            "enquiry_id": "SN-OPEN-DUP",
            "account_key": "ACCT-99",
            "t0_time": (now - timedelta(days=2)).isoformat(),
            "extraction_time": ext_old,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
        {
            "enquiry_id": "SN-OVER-60D",
            "account_key": "ACCT-99",
            "t0_time": (now - timedelta(days=80)).isoformat(),
            "extraction_time": ext_new,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
    ])
    _write_csv(out_csv, [
        {
            "enquiry_id": eid,
            "event_type": "Observation_Ended",
            "event_time": ext_new,
            "maturity_state": "MATURE",
            "label": "POSITIVE",
            "observation_window_days": 60,
            "cohort": "TRAINING_CANDIDATE",
        }
        for eid in ["SN-OLD-CLOSED", "SN-NEW-OPEN", "SN-OPEN-DUP", "SN-OVER-60D"]
    ])
    csv_validator.validate_and_load()
    stats = deduplicator.deduplicate_enquiries(db_path)
    # SN-OLD-CLOSED giữ nguyên, SN-OVER-60D tách cụm riêng (>60d), SN-OPEN-DUP + SN-NEW-OPEN gộp làm 1 (lấy extraction_time mới nhất = ext_new)
    assert stats["clean_count"] == 3
    assert stats["duplicates_count"] == 1

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        with conn:
            pk_info = conn.execute("PRAGMA table_info(clean_enquiries)").fetchall()
            assert any(col[1] == "enquiry_id" and col[5] == 1 for col in pk_info)
            ext_merged = conn.execute(
                "SELECT extraction_time FROM clean_enquiries WHERE enquiry_id='SN-OPEN-DUP'"
            ).fetchone()[0]
            assert ext_merged == ext_new

    client = TestClient(app)
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    ids = [i["enquiry_id"] for i in res["items"]]
    assert "SN-OPEN-DUP" in ids
    assert "SN-OVER-60D" in ids
    assert "SN-OLD-CLOSED" not in ids


def test_block_single_enquiry_even_when_account_key_sent(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext = (now - timedelta(hours=1)).isoformat()

    _write_csv(enq_csv, [
        {
            "enquiry_id": "SN-A1",
            "account_key": "ACCT-A",
            "t0_time": (now - timedelta(days=2)).isoformat(),
            "extraction_time": ext,
            "product_family": "Motor_DongCo",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
        {
            "enquiry_id": "SN-A2",
            "account_key": "ACCT-A",
            "t0_time": (now - timedelta(days=1)).isoformat(),
            "extraction_time": ext,
            "product_family": "Drive_BienTan",
            "spec_completeness": "Full",
            "workflow_state": "New",
        },
    ])
    _write_csv(out_csv, [
        {
            "enquiry_id": eid,
            "event_type": "Observation_Ended",
            "event_time": ext,
            "maturity_state": "MATURE",
            "label": "POSITIVE",
            "observation_window_days": 60,
            "cohort": "TRAINING_CANDIDATE",
        }
        for eid in ["SN-A1", "SN-A2"]
    ])
    csv_validator.validate_and_load()
    deduplicator.deduplicate_enquiries(db_path)

    client = TestClient(app)
    # Gửi cả enquiry_id và account_key nhưng không bật block_scope="account" -> chỉ chặn SN-A1
    blk = client.post("/api/block", json={"enquiry_id": "SN-A1", "account_key": "ACCT-A", "reason": "Test single"}, headers=_auth_headers(client, "SUPERVISOR"))
    assert blk.status_code == 200
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert [i["enquiry_id"] for i in res["items"]] == ["SN-A2"]


def test_raw_outcomes_out_of_order_when_recorded_at_empty(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    t_old = (now - timedelta(days=5)).isoformat()
    t_new = (now - timedelta(days=1)).isoformat()

    _write_csv(enq_csv, [{
        "enquiry_id": "SN-OUT-1",
        "account_key": "ACCT-OUT",
        "t0_time": (now - timedelta(days=10)).isoformat(),
        "extraction_time": t_new,
        "product_family": "Motor_DongCo",
        "spec_completeness": "Full",
        "workflow_state": "New",
    }])
    # Lần 1: nạp bản mới có recorded_at = t_new, label = POSITIVE
    _write_csv(out_csv, [{
        "enquiry_id": "SN-OUT-1",
        "event_type": "Observation_Ended",
        "event_time": t_new,
        "recorded_at": t_new,
        "maturity_state": "MATURE",
        "label": "POSITIVE",
        "observation_window_days": 60,
        "cohort": "TRAINING_CANDIDATE",
    }])
    csv_validator.validate_and_load()

    # Lần 2: nạp bản cũ có recorded_at = "" và event_time = t_old, label = NEGATIVE -> không được ghi đè
    _write_csv(out_csv, [{
        "enquiry_id": "SN-OUT-1",
        "event_type": "Observation_Ended",
        "event_time": t_old,
        "recorded_at": "",
        "maturity_state": "MATURE",
        "label": "NEGATIVE",
        "observation_window_days": 60,
        "cohort": "TRAINING_CANDIDATE",
    }])
    csv_validator.validate_and_load()

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        with conn:
            lbl = conn.execute("SELECT label FROM raw_outcomes WHERE enquiry_id='SN-OUT-1'").fetchone()[0]
    assert lbl == "POSITIVE"


def test_reingest_broken_active_enquiry_hides_from_queue(tmp_path, monkeypatch):
    enq_csv, out_csv, _, _, db_path = _configure_paths(tmp_path, monkeypatch)
    now = datetime.now(VN_TZ).replace(microsecond=0)
    ext = (now - timedelta(hours=1)).isoformat()

    _write_csv(enq_csv, [{
        "enquiry_id": "SN-REINGEST",
        "account_key": "ACCT-R",
        "t0_time": (now - timedelta(days=2)).isoformat(),
        "extraction_time": ext,
        "product_family": "Motor_DongCo",
        "spec_completeness": "Full",
        "workflow_state": "New",
    }])
    _write_csv(out_csv, [{
        "enquiry_id": "SN-REINGEST",
        "event_type": "Observation_Ended",
        "event_time": ext,
        "maturity_state": "MATURE",
        "label": "POSITIVE",
        "observation_window_days": 60,
        "cohort": "TRAINING_CANDIDATE",
    }])
    csv_validator.validate_and_load()
    deduplicator.deduplicate_enquiries(db_path)

    # Lần nạp lại: SN-REINGEST bị hỏng contract_due_date -> quarantine & ẩn khỏi clean_enquiries và /api/queue
    _write_csv(enq_csv, [{
        "enquiry_id": "SN-REINGEST",
        "account_key": "ACCT-R",
        "t0_time": (now - timedelta(days=2)).isoformat(),
        "extraction_time": ext,
        "product_family": "Motor_DongCo",
        "contract_due_date": "bad-date",
        "spec_completeness": "Full",
        "workflow_state": "New",
    }])
    csv_validator.validate_and_load()

    client = TestClient(app)
    res = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert res["total"] == 0
    assert res["items"] == []
    assert res["quarantine_count"] == 1  # SN-REINGEST gộp trùng giữa quarantine_rows và quarantine_outcomes

