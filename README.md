# SongNhat AI-02: Predictive Lead Prioritization (PLPT)

Hệ thống hỗ trợ điều phối và xếp hạng ưu tiên yêu cầu báo giá B2B (động cơ điện, biến tần, hộp số) cho doanh nghiệp **Song Nhật**, áp dụng chiến lược **Rules-First Baseline** (chấm điểm tất định, minh bạch, có con người kiểm soát và không dùng thông tin định danh cá nhân PII để chấm điểm).

- **Jira Workspace:** [PLPT Backlog](https://www.google.com/search?q=https://mjnkhieu3012.atlassian.net/jira/software/projects/PLPT/boards/34/backlog)
- **Ngôn ngữ & Stack chuẩn:** Python 3.11+, FastAPI, Pandas, SQLite (`data/songnhat.db`), Pytest.

---

## 1. Cấu trúc thư mục chuẩn

```text
predictive-lead-prioritization/
├── data/
│   ├── raw/
│   │   ├── enquiries_snapshot.csv      # 22 cột (UTF-8)
│   │   └── outcome_events.csv          # 14 cột (UTF-8)
│   ├── quarantine/                     # File lỗi sinh tự động (ignored by Git)
│   └── songnhat.db                     # SQLite DB sinh tự động (ignored by Git)
├── docs/
│   ├── us_01_dinh_nghia_thoi_gian_va_cong_suat.md
│   ├── us_02_tieu_chi_rubric_va_trang_thai.md
│   ├── us_03_cau_truc_2_file_csv.md
│   ├── us_04_bao_mat_va_phap_ly_vietnam.md
│   └── us_05_ho_so_quyet_dinh_go.md
├── scripts/
│   ├── generate_songnhat_data.py       # Sinh dữ liệu mẫu chuẩn 22 cột / 14 cột
│   └── us_03_check.py                  # Kiểm tra schema & dữ liệu quarantine
├── src/
│   ├── api/
│   │   └── main.py                     # FastAPI endpoint GET /api/queue
│   ├── ingest/
│   │   ├── csv_validator.py            # Kiểm tra schema, tách quarantine, nạp raw_*
│   │   └── deduplicator.py             # Gộp trùng đa kênh -> clean_enquiries & merge_audit_log
│   └── rules/
│       └── rules_engine.py             # Chấm điểm RulesScore (0-100) & xếp hạng Tier 0
├── tests/
│   ├── test_deduplicator.py
│   └── test_rules_engine.py
├── .gitignore
├── .pre-commit-config.yaml
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
# 1. Sinh dữ liệu mẫu chuẩn
py scripts/generate_songnhat_data.py

# 2. Chạy pipeline nạp CSV và gộp trùng
py -m src.ingest.csv_validator
py -m src.ingest.deduplicator

# 3. Chạy toàn bộ unit test
py -m pytest

# 4. Khởi động FastAPI server
py -m uvicorn src.api.main:app --reload
```

Truy cập tài liệu API tương tác tại: `http://127.0.0.1:8000/docs` hoặc kiểm tra hàng đợi tại `http://127.0.0.1:8000/api/queue`.
