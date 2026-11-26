"""Tests cho PLPT-24 & PLPT-25: Capacity K, 10% Exploration, Overdue 2BD, RBAC 5 vai trò & Telemetry."""

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


from src.api.main import app

VN_TZ = timezone(timedelta(hours=7))


def _seed_workspace_db(db_path: Path, count: int = 30, urgent_count: int = 2, days_ago_t0: int = 5) -> None:
    now = datetime.now(VN_TZ)
    ext_iso = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
    t0_iso = (now - timedelta(days=days_ago_t0)).replace(microsecond=0).isoformat()

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                """
                CREATE TABLE clean_enquiries (
                    enquiry_id TEXT PRIMARY KEY,
                    account_key TEXT NOT NULL,
                    t0_time TEXT NOT NULL,
                    extraction_time TEXT NOT NULL,
                    product_family TEXT NOT NULL,
                    project_ref_code TEXT,
                    spec_completeness TEXT,
                    is_urgent_replacement TEXT,
                    contract_due_date TEXT,
                    workflow_state TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE raw_outcomes (
                    enquiry_id TEXT PRIMARY KEY,
                    label TEXT
                )
                """
            )
            rows = []
            for i in range(1, count + 1):
                is_urg = "Urgent" if i <= urgent_count else "Normal"
                prod = "Motor_DongCo" if i % 2 == 1 else "Drive_BienTan"
                rows.append(
                    (
                        f"SN-{i:04d}",
                        f"ACCT-{i:04d}",
                        t0_iso,
                        ext_iso,
                        prod,
                        f"PRJ-{i:04d}",
                        "Full" if i % 3 != 0 else "Minimal",
                        is_urg,
                        (now + timedelta(days=15)).date().isoformat(),
                        "New",
                    )
                )
            conn.executemany(
                "INSERT INTO clean_enquiries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()


def test_workspace_k_allocation_and_10pct_exploration(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ws.db"
    _seed_workspace_db(db_path, count=30, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "20")
    monkeypatch.setenv("EXPLORATION_SEED", "42")

    client = TestClient(app)
    res = client.get("/api/v1/workspace/queue?limit=50", headers=_auth_headers(client, "SALES_READER"))
    assert res.status_code == 200
    body = res.json()
    assert body["capacity_k"] == 20
    assert body["total"] == 20
    assert body["exploration_slots_allocated"] == 2
    assert body["exploration_seed"] == 42
    assert body["scoring_mode"] == "RULES_BASELINE"
    assert body["is_overloaded"] is False
    items = body["items"]
    assert len(items) == 20
    explore_items = [x for x in items if x["allocation_type"] == "EXPLORATION_10PCT"]
    assert len(explore_items) == 2
    for field in ("model_rank", "final_work_order", "snapshot_age_hours", "scoring_mode", "sla_due_timestamp"):
        assert field in items[0]


def test_workspace_tier0_overload_sets_zero_exploration(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "overload.db"
    _seed_workspace_db(db_path, count=30, urgent_count=26, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "25")

    client = TestClient(app)
    res = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "SUPERVISOR"))
    assert res.status_code == 200
    body = res.json()
    assert body["capacity_k"] == 25
    assert body["status"] == "CAPACITY_EXCEEDED"
    assert body["capacity_exceeded_by"] == 1
    assert body["total"] == 26
    assert body["exploration_slots_allocated"] == 0
    assert body["is_overloaded"] is True
    assert all(item["is_urgent"] is True for item in body["items"])


def test_workspace_rbac_401_403_and_vn_diacritic_search(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "rbac.db"
    _seed_workspace_db(db_path, count=10, urgent_count=1, days_ago_t0=6)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))

    client = TestClient(app)

    # 401 khi thiếu auth hoặc phiên hết hạn
    assert client.get("/api/v1/workspace/queue").status_code == 401
    assert client.get(
        "/api/v1/workspace/queue",
        headers=_auth_headers(client, "SALES_READER", expired=True),
    ).status_code == 401

    # 403 khi vai trò không được phép
    assert client.post(
        "/api/v1/workspace/override",
        json={"enquiry_id": "SN-0001", "override_reason_code": "OTHER_VERIFIED", "justification": "test"},
        headers=_auth_headers(client, "DATA_STEWARD"),
    ).status_code == 403
    assert client.post(
        "/api/block",
        json={"enquiry_id": "SN-0001"},
        headers=_auth_headers(client, "SALES_READER"),
    ).status_code == 403

    # Tìm kiếm không dấu tiếng Việt: "dong co" khớp "Motor_DongCo"
    res = client.get(
        "/api/v1/workspace/queue?q=dong%20co",
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) > 0
    assert all(item["product_family"] == "Motor_DongCo" for item in items)


def test_workspace_override_and_review_isolation(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "override.db"
    _seed_workspace_db(db_path, count=5, urgent_count=0, days_ago_t0=6)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))

    client = TestClient(app)

    # Override thiếu justification -> 400
    bad = client.post(
        "/api/v1/workspace/override",
        json={"enquiry_id": "SN-0001", "override_reason_code": "CUSTOMER_URGENT_CALLBACK", "justification": ""},
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert bad.status_code == 400

    # Override hợp lệ -> ghi staff_override_logs, không chạm raw_outcomes
    ok = client.post(
        "/api/v1/workspace/override",
        json={
            "enquiry_id": "SN-0001",
            "override_reason_code": "CUSTOMER_URGENT_CALLBACK",
            "justification": "Khach goi gap thay the motor dung chuyen",
        },
        headers=_auth_headers(client, "SALES_READER", user_id="sales_01"),
    )
    assert ok.status_code == 200
    assert ok.json()["isolated_from_outcome_events"] is True

    # Review bắt buộc xác minh contact_permission_verified nếu contact_attempted=true
    bad_rev = client.post(
        "/api/v1/workspace/review",
        json={"enquiry_id": "SN-0001", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": False, "contact_attempted": True},
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert bad_rev.status_code == 400

    ok_rev = client.post(
        "/api/v1/workspace/review",
        json={"enquiry_id": "SN-0001", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": True, "contact_attempted": True},
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert ok_rev.status_code == 200

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            assert conn.execute("SELECT COUNT(*) FROM staff_override_logs").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM staff_review_logs").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0] == 0


def test_serving_mode_locked_to_fallback_until_gate_g1(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "mode.db"
    _seed_workspace_db(db_path, count=5, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("ACTIVE_SCORING_MODE", "PREDICTIVE_ML")
    monkeypatch.setenv("GATE_G1_APPROVED", "false")

    client = TestClient(app)
    res = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "MODEL_REVIEWER"))
    assert res.status_code == 200
    assert res.json()["scoring_mode"] == "FALLBACK_RULES_BASELINE"


def test_workspace_block_alias_syncs_both_queues(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "block_sync.db"
    _seed_workspace_db(db_path, count=5, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))

    client = TestClient(app)
    res_block = client.post(
        "/api/v1/workspace/block",
        json={"enquiry_id": "SN-0001", "reason": "Test alias block"},
        headers=_auth_headers(client, "SUPERVISOR"),
    )
    assert res_block.status_code == 200
    assert res_block.json()["blocked"] is True

    q_legacy = client.get("/api/queue", headers=_auth_headers(client, "SALES_READER")).json()
    q_v1 = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert q_legacy["total"] == 4
    assert q_v1["total"] == 4
    assert all(item["enquiry_id"] != "SN-0001" for item in q_legacy["items"])
    assert all(item["enquiry_id"] != "SN-0001" for item in q_v1["items"])


def test_workspace_edge_cases_k_and_pagination(tmp_path: Path, monkeypatch):
    client = TestClient(app)

    # Case 1: total < K (total=8, K=25) -> trả đủ 8 hồ sơ, không lỗi
    db_lt = tmp_path / "lt_k.db"
    _seed_workspace_db(db_lt, count=8, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_lt))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "25")
    r1 = client.get("/api/v1/workspace/queue?limit=50", headers=_auth_headers(client, "SALES_READER")).json()
    assert r1["total"] == 8 and len(r1["items"]) == 8 and r1["exploration_slots_allocated"] == 0

    # Case 2: K=3 (< 5) -> exploration_slots_allocated = 0
    db_k3 = tmp_path / "k3.db"
    _seed_workspace_db(db_k3, count=10, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_k3))
    monkeypatch.setenv("DAILY_CAPACITY_K", "3")
    r2 = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert r2["capacity_k"] == 3 and r2["total"] == 3 and r2["exploration_slots_allocated"] == 0

    # Case 3: thiếu ứng viên exploration (K=25 -> K_ranked=23, total=23 -> remaining_pool=0 -> explore=0)
    db_no_pool = tmp_path / "no_pool.db"
    _seed_workspace_db(db_no_pool, count=23, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_no_pool))
    monkeypatch.setenv("DAILY_CAPACITY_K", "25")
    r3 = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "SALES_READER")).json()
    assert r3["total"] == 23 and r3["exploration_slots_allocated"] == 0

    # Case 4: limit=10 khi K=25 (total=30) -> chỉ cắt trang 10 item, tập K vẫn giữ 25 (exploration=round(0.1*25)=2)
    db_pag = tmp_path / "pag.db"
    _seed_workspace_db(db_pag, count=30, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_pag))
    monkeypatch.setenv("DAILY_CAPACITY_K", "25")
    r4 = client.get("/api/v1/workspace/queue?page=1&limit=10", headers=_auth_headers(client, "SALES_READER")).json()
    assert r4["capacity_k"] == 25
    assert r4["total"] == 25
    assert r4["exploration_slots_allocated"] == 2
    assert len(r4["items"]) == 10


def test_workspace_override_reorders_final_work_order_and_tab_separation(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ov_order.db"
    _seed_workspace_db(db_path, count=6, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "5")

    client = TestClient(app)
    before = client.get("/api/v1/workspace/queue?view=active", headers=_auth_headers(client, "SALES_READER")).json()["items"]
    last_item = before[-1]
    assert last_item["final_work_order"] == 5

    # Ghi đè hồ sơ ở vị trí #5 -> final_work_order phải nhảy lên #1, còn model_rank giữ nguyên
    ov = client.post(
        "/api/v1/workspace/override",
        json={"enquiry_id": last_item["enquiry_id"], "override_reason_code": "CUSTOMER_URGENT_CALLBACK", "justification": "Uu tien goi lai"},
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert ov.status_code == 200

    after = client.get("/api/v1/workspace/queue?view=active", headers=_auth_headers(client, "SALES_READER")).json()["items"]
    promoted = [x for x in after if x["enquiry_id"] == last_item["enquiry_id"]][0]
    assert promoted["final_work_order"] == 1
    assert promoted["model_rank"] == last_item["model_rank"]
    assert promoted["rules_override"] is True

    # Kiểm tra Active và Unscored không trùng hồ sơ
    unsc = client.get("/api/v1/workspace/queue?view=unscored", headers=_auth_headers(client, "SALES_READER")).json()["items"]
    act_ids = {x["enquiry_id"] for x in after}
    unsc_ids = {x["enquiry_id"] for x in unsc}
    assert len(act_ids & unsc_ids) == 0


def test_workspace_all_5_roles_and_100_req_5_sessions_p95(tmp_path: Path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time
    import numpy as np

    db_path = tmp_path / "perf.db"
    _seed_workspace_db(db_path, count=30, urgent_count=2, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))

    client = TestClient(app)
    for role in ("SALES_READER", "SUPERVISOR", "DATA_STEWARD", "MODEL_REVIEWER", "ADMINISTRATOR"):
        res = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, role))
        assert res.status_code == 200

    # Kiểm tra đo tải 100 request qua 5 phiên đồng thời p95 <= 3.0s
    def _call(_):
        t0 = time.perf_counter()
        r = client.get("/api/v1/workspace/queue", headers=_auth_headers(client, "SALES_READER"))
        assert r.status_code == 200
        return time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=5) as ex:
        latencies = list(ex.map(_call, range(100)))
    p95 = float(np.percentile(latencies, 95))
    assert p95 <= 3.0


def test_override_outside_top_k_and_write_guards(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "ov_outside.db"
    _seed_workspace_db(db_path, count=10, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "5")
    monkeypatch.setenv("EXPLORATION_SEED", "42")

    client = TestClient(app)
    all_items = client.get("/api/v1/workspace/queue?view=active&include_overflow=true&limit=50", headers=_auth_headers(client, "SALES_READER")).json()["items"]
    assert len(all_items) == 7
    unsc_items = client.get("/api/v1/workspace/queue?view=unscored", headers=_auth_headers(client, "SALES_READER")).json()["items"]
    assert len(unsc_items) == 3 and all(x["spec_completeness"] == "Minimal" for x in unsc_items)
    overflow_one = [x for x in all_items if x["allocation_type"] == "OVERFLOW_OUTSIDE_K"][0]

    # Override một hồ sơ nằm ngoài Top-K -> phải được kéo lên Active ngay sau TIER_0 (vị trí #1) và giữ nguyên model_rank
    ov = client.post(
        "/api/v1/workspace/override",
        json={"enquiry_id": overflow_one["enquiry_id"], "override_reason_code": "CUSTOMER_URGENT_CALLBACK", "justification": "Khach goi gap"},
        headers=_auth_headers(client, "SALES_READER"),
    )
    assert ov.status_code == 200

    top_k = client.get("/api/v1/workspace/queue?view=active", headers=_auth_headers(client, "SALES_READER")).json()
    assert top_k["total"] == 5
    assert top_k["items"][0]["enquiry_id"] == overflow_one["enquiry_id"]
    assert top_k["items"][0]["final_work_order"] == 1
    assert top_k["items"][0]["model_rank"] == overflow_one["model_rank"]

    # Hồ sơ không tồn tại -> 404
    assert client.post(
        "/api/v1/workspace/review",
        json={"enquiry_id": "NOT-EXIST", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": True},
        headers=_auth_headers(client, "SALES_READER"),
    ).status_code == 404

    # Hồ sơ bị block -> 409
    assert client.post("/api/block", json={"enquiry_id": "SN-0002"}, headers=_auth_headers(client, "SUPERVISOR")).status_code == 200
    assert client.post(
        "/api/v1/workspace/override",
        json={"enquiry_id": "SN-0002", "override_reason_code": "OTHER_VERIFIED", "justification": "test"},
        headers=_auth_headers(client, "SALES_READER"),
    ).status_code == 409

    # Đồng bộ is_overloaded ở Unscored và chặn ghi khi HOLD_STALE_DATA (423)
    db_stale = tmp_path / "stale_guard.db"
    _seed_workspace_db(db_stale, count=6, urgent_count=6, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_stale))
    monkeypatch.setenv("DAILY_CAPACITY_K", "5")
    unsc_res = client.get("/api/v1/workspace/queue?view=unscored", headers=_auth_headers(client, "SALES_READER")).json()
    assert unsc_res["is_overloaded"] is True

    with contextlib.closing(sqlite3.connect(str(db_stale))) as conn:
        with conn:
            stale_ext = (datetime.now(VN_TZ) - timedelta(hours=30)).replace(microsecond=0).isoformat()
            conn.execute("UPDATE clean_enquiries SET extraction_time = ?", (stale_ext,))
            conn.commit()
    assert client.post(
        "/api/v1/workspace/review",
        json={"enquiry_id": "SN-0001", "decision_type": "ACCEPT_PRIORITY", "contact_permission_verified": True},
        headers=_auth_headers(client, "SALES_READER"),
    ).status_code == 423


def test_fractional_carry_across_consecutive_days_and_idempotency(tmp_path: Path, monkeypatch):
    from src.services.queue_service import build_workspace_queue_payload

    # D = 5 qua 4 ngày liên tiếp: quota_tenths = 5, 10, 5, 10 -> exploration = 0, 1, 0, 1
    db_path = tmp_path / "carry.db"
    _seed_workspace_db(db_path, count=15, urgent_count=0, days_ago_t0=1)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("QUARANTINE_DIR", str(tmp_path))
    monkeypatch.setenv("DAILY_CAPACITY_K", "5")
    monkeypatch.delenv("EXPLORATION_SEED", raising=False)

    # Cập nhật extraction_time đủ mới cho cả 4 ngày test
    base_day = datetime(2026, 11, 10, 9, 0, 0, tzinfo=VN_TZ)
    explore_counts = []
    carry_outs = []

    for d_idx in range(4):
        day_now = base_day + timedelta(days=d_idx)
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                conn.execute(
                    "UPDATE clean_enquiries SET extraction_time = ?, t0_time = ?",
                    (
                        (day_now - timedelta(hours=1)).isoformat(),
                        (day_now - timedelta(hours=5)).isoformat(),
                    ),
                )
                conn.commit()
        p1 = build_workspace_queue_payload(view="active", capacity_k=5, now=day_now)
        p2 = build_workspace_queue_payload(view="active", capacity_k=5, now=day_now)
        # Idempotent trong cùng ngày: trả lại đúng seed & danh sách đã chốt
        assert p1["exploration_seed"] == p2["exploration_seed"]
        assert [x["enquiry_id"] for x in p1["items"]] == [x["enquiry_id"] for x in p2["items"]]
        assert p1["status"] == "OK"
        explore_counts.append(p1["exploration_slots_allocated"])
        carry_outs.append(p1["carry_out_tenths"])

    assert explore_counts == [0, 1, 0, 1]
    assert carry_outs == [5, 0, 5, 0]

