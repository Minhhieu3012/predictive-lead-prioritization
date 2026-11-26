# SongNhat AI-02: Predictive Lead Prioritization (PLPT)

Hệ thống hỗ trợ điều phối và xếp hạng ưu tiên yêu cầu báo giá B2B (động cơ điện, biến tần, hộp số) cho doanh nghiệp **Song Nhật**, áp dụng chiến lược **Rules-First Baseline** (chấm điểm tất định, minh bạch, có con người kiểm soát và không dùng thông tin định danh cá nhân PII để chấm điểm).

- **Jira Workspace:** [PLPT Backlog](https://www.google.com/search?q=https://mjnkhieu3012.atlassian.net/jira/software/projects/PLPT/boards/34/backlog)
- **Ngôn ngữ & Stack chuẩn:** Python 3.11+, FastAPI, Pandas, SQLite (`data/songnhat.db`), Pytest.

---

## 1. Cấu trúc thư mục chuẩn

```text
predictive-lead-prioritization/
├── artifacts/                          # Bằng chứng kiểm toán bất biến theo từng Cổng kiểm soát
│   ├── gate_g1/                        # Báo cáo 40 Control Fixtures, benchmark & khóa single-use cohort
│   ├── gate_g2/                        # Bằng chứng kiểm thử Shadow Scoring, RBAC & phê duyệt Gate G2
│   ├── gate_g3/                        # Bằng chứng thí điểm 20 ngày làm việc & nghiệm thu Gate G3
│   ├── models/
│   │   └── plpt-logreg-v1.0.0/         # Manifest ứng viên ML (serving_mode_locked: RULES_BASELINE)
│   └── reference/                      # Chuẩn đối chiếu drift hằng ngày (PLPT-58 / OPS-06)
├── data/                               # Dữ liệu cục bộ/vận hành (chỉ commit .gitkeep; mọi CSV/DB bị .gitignore)
│   ├── raw/                            # enquiries_snapshot.csv (22 cột) & outcome_events.csv (14 cột)
│   ├── quarantine/                     # File lỗi tách riêng (quarantine_rows.csv, quarantine_outcomes.csv)
│   ├── backups/                        # Bản sao lưu SQLite xoay vòng 35 ngày (Sprint 5 / OPS-07)
│   └── songnhat.db                     # SQLite DB sinh tự động (ignored by Git)
├── docs/
│   ├── README.md                       # Tổng mục lục phân tầng 4 nhóm tài liệu RFP
│   ├── discovery/                      # Gói bàn giao DEL 01 (Sprint 1: us_01–us_05)
│   │   ├── us_01_dinh_nghia_thoi_gian_va_cong_suat.md
│   │   ├── us_02_tieu_chi_rubric_va_trang_thai.md
│   │   ├── us_03_cau_truc_2_file_csv.md
│   │   ├── us_04_bao_mat_va_phap_ly_vietnam.md
│   │   └── us_05_ho_so_quyet_dinh_go.md
│   ├── specs/                          # Đặc tả & quyết định kỹ thuật (Sprint 3–4)
│   │   ├── PO-Decisions-Daily-Lead-Workspace.md
│   │   └── ML-Pre-T0-Feature-Store-Decisions.md
│   ├── governance/                     # Biên bản chốt cổng Gate G1–G3 & tuân thủ pháp lý VN
│   │   └── README.md
│   └── handover/                       # Gói bàn giao DEL 04 (Sprint 5: hướng dẫn 5 vai trò, vận hành, exit)
│       └── README.md
├── scripts/
│   ├── dev/                            # [SYNTHETIC_ONLY] Chỉ sinh dữ liệu giả lập kiểm thử, KHÔNG dùng cho dữ liệu thật
│   │   └── generate_synthetic_data.py  # [SYNTHETIC_ONLY] Sinh dữ liệu mẫu chuẩn 22 cột / 14 cột
│   └── audit/                          # Script kiểm toán dữ liệu & đánh giá cổng Gate G0/G1
│       ├── us_03_check.py              # Kiểm toán schema, độ chín 60 ngày, chống rò rỉ & quarantine
│       ├── run_control_fixtures.py     # Chạy 40 Control Fixtures -> artifacts/gate_g1/control_fixtures_report.json
│       └── run_gate_g1_eval.py         # Đánh giá Temporal Benchmark, Paired Bootstrap & Gate G1
├── src/
│   ├── api/
│   │   └── main.py                     # FastAPI endpoint GET /api/queue & /api/v1/workspace/*
│   ├── config/
│   │   └── vn_holidays.py              # Lịch ngày làm việc & ngày lễ Việt Nam
│   ├── ingest/
│   │   ├── csv_validator.py            # Kiểm tra schema, tách quarantine, nạp raw_*
│   │   ├── deduplicator.py             # Gộp trùng đa kênh -> clean_enquiries & merge_audit_log
│   │   └── outcome_maturity.py         # Đánh giá độ chín nhãn 60 ngày & hiệu chỉnh nhãn có phiên bản
│   ├── rules/                          # Lõi Rules-First (mặc định phục vụ)
│   │   └── rules_engine.py             # Chấm điểm RulesScore (0-100) & xếp hạng Tier 0
│   ├── services/
│   │   └── queue_service.py            # Phân bổ công suất K, 10% exploration, overdue 2BD & telemetry
│   ├── features/
│   │   └── pre_t0_store.py             # Kho đặc trưng pre-t0 chống rò rỉ tương lai
│   ├── models/                         # Nhánh ML có điều kiện (chỉ kích hoạt sau Gate G1)
│   │   ├── train_candidates.py         # Huấn luyện ứng viên Logistic Regression / Decision Tree
│   │   ├── explainer.py                # Giải thích top-3 yếu tố đóng góp phi nhân quả
│   │   ├── shadow_runner.py            # PLPT-56: Chấm điểm ngầm (Shadow Scoring), tách khỏi outcome_events
│   │   └── serving_router.py           # PLPT-56: Bộ định tuyến mặc định RULES_BASELINE & tự động fallback
│   ├── evaluation/
│   │   ├── temporal_benchmark.py       # Đánh giá theo lát cắt thời gian & khóa single-use cohort
│   │   ├── paired_bootstrap.py         # Khoảng tin cậy ghép cặp 95% theo cụm tài khoản
│   │   └── gate_g1_decider.py          # Cổng quyết định Gate G1 (G1-C1..G1-C7)
│   ├── security/                       # Bảo mật, xác thực 5 vai trò RBAC & khóa sự cố 4 giờ
│   │   ├── auth.py                     # Xác thực phiên 5 vai trò RBAC & giới hạn 5 phiên đồng thời
│   │   ├── rbac.py                     # Ma trận phân quyền 5 vai trò RBAC (SEC-01)
│   │   └── incident_hold.py            # Chặn khẩn cấp cục bộ & khóa ứng phó sự cố 4 giờ (SEC-08)
│   ├── monitoring/                     # Giám sát vận hành hằng ngày (Sprint 4)
│   │   └── drift_monitor.py            # PLPT-58 / OPS-06: Giám sát trôi dạt, tỷ lệ thiếu & độ bao phủ
│   ├── pilot/                          # Theo dõi thử nghiệm có giám sát (Sprint 4)
│   │   └── pilot_tracker.py            # PLPT-57: Phân bổ theo tài khoản & đo công sức 20 ngày làm việc
│   └── ops/                            # Vận hành & bàn giao (Sprint 5)
│       ├── backup_restore.py           # OPS-07: Sao lưu 35 ngày & khôi phục tái áp dụng lệnh chặn
│       └── portable_exit.py            # OPS-08 / DEL-04: Xuất dữ liệu rời bỏ hệ thống định dạng trung lập
├── tests/
│   ├── core_pipeline/                  # Sprint 2: pipeline sync, dedup, rules engine, emergency block
│   │   ├── test_pipeline_sync.py
│   │   ├── test_deduplicator.py
│   │   ├── test_rules_engine.py
│   │   └── test_plpt17_blocking.py
│   ├── workspace_pilot/                # Sprint 3–4 (PLPT-4 / PLPT-57): workspace, p95 <= 3.0s, pilot 20BD
│   │   ├── test_sprint3_workspace.py
│   │   └── test_plpt57_pilot_and_perf.py
│   ├── ml_shadow/                      # Sprint 3–4 (PLPT-5 / PLPT-56): eval, 1.000/200 split, shadow & fallback
│   │   ├── test_sprint3_ml_eval.py
│   │   └── test_plpt56_shadow_and_fallback.py
│   ├── security_ops/                   # Sprint 4–5 (PLPT-7, PLPT-8): RBAC 5 vai trò, sự cố 4h, drift & backup/exit
│   │   ├── test_plpt55_rbac_and_incident.py
│   │   └── test_plpt58_drift_and_ops.py
│   └── control_fixtures/               # Cổng Gate G1 & kiểm thử hồi quy (40 Control Fixtures + CF-09)
│       ├── fixtures_data.py
│       ├── test_gate_g1_controls.py
│       └── test_cf09_steward_dedup.py
├── .env.example                        # Mẫu biến môi trường (ENV_MODE, DATABASE_PATH, SESSION_SECRET_KEY, SESSION_TTL_MINUTES)
├── .gitignore                          # Chặn tuyệt đối .env thật, DB SQLite, CSV dữ liệu thật & quarantine
├── .pre-commit-config.yaml             # Hook gitleaks + chặn commit file .env thật
├── requirements.txt
└── README.md
```

---

## 2. Quy ước dữ liệu & Luật nghiệp vụ khóa cứng

1. **Chuẩn cột CSV & SQLite:**
   - `enquiries_snapshot.csv` gồm **22 cột** (UTF-8), dùng chuẩn tên cột `contract_due_date` (không dùng `contract_due_at_t0`).
   - Khi nạp vào SQLite (`raw_enquiries` và `clean_enquiries`), hệ thống tự động loại bỏ 3 cột `*_DO_NOT_USE` (`contact_phone_DO_NOT_USE`, `latest_quote_value_post_t0_DO_NOT_USE`, `latest_quote_available_at_DO_NOT_USE`), còn lại **19 cột** sạch.
   - `outcome_events.csv` và bảng `raw_outcomes` gồm **14 cột**.
2. **Quy tắc gộp trùng (Deduplication — `PLPT-15`):**
   - Gộp các bản ghi trong `raw_enquiries` theo cặp `(account_key, product_family)`.
   - Giữ bản ghi có `t0_time` sớm nhất làm bản ghi gốc (`canonical`), bù đắp các trường còn thiếu từ bản ghi trùng và ghi lịch sử vào bảng duy nhất **`merge_audit_log`**.
3. **Quy tắc xếp hạng hàng đợi (`PLPT-16`):**
   - Loại bỏ các yêu cầu đã đóng (`workflow_state = 'Closed'`) khỏi hàng đợi phục vụ.
   - **Tier 0 (Khẩn cấp):** `is_urgent_replacement = 'Urgent'` hoặc `contract_due_date` còn `0–7` ngày kể từ `t0_time`.
   - **Thứ tự sắp xếp:** `Tier 0` → `rules_score` giảm dần (tối đa 100 điểm) → FIFO theo `t0_time` tăng dần → `enquiry_id` tăng dần.
4. **Kiểm soát an toàn (`PLPT-17` / US-09):**
   - `GET /api/queue` trả về `status = "HOLD_STALE_DATA"` nếu dữ liệu nguồn cũ hơn `24 giờ`, ngược lại trả về `"OK"`, kèm cờ `is_blocked` đối chiếu bảng `blocked_accounts`.

---

## 3. Hướng dẫn chạy trên máy cá nhân (Windows)

Mở PowerShell tại thư mục gốc dự án và sử dụng `py`:

```powershell
# 1. [SYNTHETIC_ONLY] Sinh dữ liệu giả lập để kiểm thử pipeline cục bộ (KHÔNG dùng cho dữ liệu thật)
py scripts/dev/generate_synthetic_data.py

# 2. Chạy kiểm toán dữ liệu đầu vào (US-03) & 40 Control Fixtures
py scripts/audit/us_03_check.py
py scripts/audit/run_control_fixtures.py

# 3. Chạy pipeline nạp CSV và gộp trùng
py -m src.ingest.csv_validator
py -m src.ingest.deduplicator

# 4. Chạy toàn bộ unit test
py -m pytest

# 5. Khởi động FastAPI server
py -m uvicorn src.api.main:app --reload
```

Truy cập tài liệu API tương tác tại: `http://127.0.0.1:8000/docs` hoặc kiểm tra hàng đợi tại `http://127.0.0.1:8000/api/queue`.


