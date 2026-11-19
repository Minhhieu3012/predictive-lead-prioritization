# SongNhat AI-02: Predictive Lead Prioritization (PLPT)

Hệ thống hỗ trợ điều phối và xếp hạng ưu tiên yêu cầu báo giá B2B (`Motor_DongCo` — Động cơ điện, `Drive_BienTan` — Biến tần, `Transmission_HopSo` — Hộp số truyền động) cho doanh nghiệp **Song Nhật (SongNhat)**, áp dụng chiến lược **Rules-First Baseline** (chấm điểm tất định, minh bạch, 100% **Human-in-the-loop**, không dùng LLM/GenAI và không dùng thông tin định danh cá nhân PII để chấm điểm).

- **Jira Workspace:** [PLPT Backlog](https://mjnkhieu3012.atlassian.net/jira/software/projects/PLPT/boards/34/backlog)
- **Ngôn ngữ & Stack chuẩn:** Python 3.11+, FastAPI, Pandas, SQLite (`data/songnhat.db`), Pytest, HTML/JS (`frontend/index.html`).

---

## 1. Cấu trúc thư mục chuẩn

```text
predictive-lead-prioritization/
├── data/
│   ├── raw/
│   │   ├── enquiries_snapshot.csv      # 22 cột (UTF-8, ignored by Git)
│   │   └── outcome_events.csv          # 14 cột (UTF-8, ignored by Git)
│   ├── quarantine/                     # File lỗi sinh tự động (ignored by Git)
│   └── songnhat.db                     # SQLite DB sinh tự động (ignored by Git)
├── docs/
│   ├── us_01_dinh_nghia_thoi_gian_va_cong_suat.md   # PLPT-9: Mốc t0, khung 60 ngày & công suất K
│   ├── us_02_tieu_chi_rubric_va_trang_thai.md       # PLPT-10: Rubric 3 điều kiện & trạng thái 60 ngày
│   ├── us_03_cau_truc_2_file_csv.md                 # PLPT-11: Từ điển dữ liệu (DAT 06) & kiểm toán AC1–AC4
│   ├── us_04_bao_mat_va_phap_ly_vietnam.md          # PLPT-12: Pháp lý VN, Controller/Processor & ứng phó 4h
│   └── us_05_ho_so_quyet_dinh_go.md                 # PLPT-13: Hồ sơ DEL 01 & quyết định Gate GO
├── frontend/
│   └── index.html                      # Giao diện hàng đợi & Chặn khẩn cấp (PLPT-17)
├── scripts/
│   ├── generate_songnhat_data.py       # Sinh dữ liệu mẫu chuẩn 22 cột / 14 cột (1.260 dòng)
│   └── us_03_check.py                  # Kiểm toán dữ liệu AC1–AC4, chống rò rỉ t0 & Quarantine
├── src/
│   ├── api/
│   │   └── main.py                     # FastAPI: GET /api/queue, POST /api/block, GET /api/health
│   ├── ingest/
│   │   ├── csv_validator.py            # Kiểm tra schema, tách quarantine, cắt *_DO_NOT_USE, nạp raw_*
│   │   └── deduplicator.py             # Gộp trùng đa kênh -> clean_enquiries & merge_audit_log
│   └── rules/
│       └── rules_engine.py             # Chấm điểm RulesScore (0–100) & xếp hạng Tier 0 (PLPT-16)
├── tests/
│   ├── test_deduplicator.py
│   ├── test_plpt17_blocking.py
│   └── test_rules_engine.py
├── .gitignore
├── .pre-commit-config.yaml
├── requirements.txt
└── README.md
```

---

## 2. Quy ước dữ liệu & Luật nghiệp vụ khóa cứng

1. **Chuẩn thời điểm `t0_time`, Khung 60 ngày & Công suất `K` (`US-01` / `PLPT-9`):**
   - `t0_time` (ISO-8601 theo giờ Việt Nam `+07:00`) là mốc đầu tiên hồ sơ đủ điều kiện trước thao tác thủ công; khóa cứng `feature_event_time <= t0_time` và `feature_available_at <= t0_time` để chống rò rỉ dữ liệu tương lai.
   - Khung quan sát cố định **60 ngày dương lịch** (`t0_time` đến `t0_time + 60 ngày`); hồ sơ chưa đủ 60 ngày giữ nguyên trạng thái `Unknown` (`IMMATURE`), không gán nhầm thành `Negative`.
   - Mức `K = 25 hồ sơ/ngày` (~`500 hồ sơ/tháng`, giảm thời gian phân loại `6 phút -> 3 phút`) là **tham số giả định minh họa đấu thầu (illustrative bid basis)** theo Annex E RFP; công suất `K` thực tế phải thống nhất với **Sales Owner** sau khi đo khối lượng CRM thật (`USR 02`, `MOD 07`).
2. **Chuẩn cột CSV, SQLite & Trạng thái kết quả (`US-02`, `US-03` / `PLPT-10`, `PLPT-11`):**
   - `enquiries_snapshot.csv` gồm **22 cột** (UTF-8), dùng chuẩn tên cột `contract_due_date` và 3 mã nhóm sản phẩm chuẩn (`Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`).
   - Khi nạp vào SQLite (`raw_enquiries` và `clean_enquiries`), hệ thống tự động loại bỏ 3 cột `*_DO_NOT_USE` (`contact_phone_DO_NOT_USE`, `latest_quote_value_post_t0_DO_NOT_USE`, `latest_quote_available_at_DO_NOT_USE`), còn lại **19 cột** sạch.
   - `outcome_events.csv` và bảng `raw_outcomes` gồm **14 cột**, tách biệt `workflow_state` với nhãn 60 ngày (`POSITIVE`, `NEGATIVE`, `Unknown`) và ghi nhận hiệu chỉnh nhãn qua `correction_status` (`DAT 05`).
3. **Quy tắc gộp trùng đa kênh (`PLPT-15`):**
   - Hợp nhất các bản ghi trong `raw_enquiries` theo khóa `(account_key, product_family, project_ref_code)` (nếu có `project_ref_code`).
   - Giữ bản ghi có `t0_time` sớm nhất làm bản ghi gốc (`canonical`), bù đắp các trường còn thiếu từ bản ghi trùng (ưu tiên giữ `is_urgent_replacement = 'Urgent'`) và ghi nhật ký vào bảng **`merge_audit_log`**.
4. **Quy tắc xếp hạng hàng đợi (`PLPT-16`):**
   - Loại bỏ các yêu cầu đã đóng (`workflow_state = 'Closed'`) khỏi hàng đợi phục vụ.
   - **Tier 0 (`TIER_0`):** `is_urgent_replacement = 'Urgent'` hoặc `contract_due_date` còn `0–7` ngày.
   - **Thứ tự sắp xếp:** `Tier 0` → `rules_score` giảm dần (thang 0–100 điểm, kèm tối đa 3 lý do chính, không hiển thị dưới dạng `%` xác suất) → FIFO theo `t0_time` tăng dần → `enquiry_id` tăng dần.
   - *Đồng bộ spec (`US-02` Mục 3):* Tài liệu đặc tả 4 tiêu chí chuẩn (`product_fit`, `spec_completeness`, `enquiry_age`, `sla_urgency`) cho `PLPT-16`; `src/rules/rules_engine.py` cần được giữ đồng bộ theo đúng bộ tiêu chí đã phê duyệt.
5. **Kiểm soát an toàn & Tuân thủ Pháp lý VN (`PLPT-17`, `US-04` / `PLPT-12`):**
   - `GET /api/queue` trả về `status = "HELD_STALE_DATA"` (`locked = true`, ẩn danh sách hàng đợi) nếu dữ liệu nguồn cũ hơn `24 giờ`; ngược lại trả về `"OK"`.
   - `POST /api/block` cho phép kích hoạt **Chặn khẩn cấp** theo `enquiry_id` và/hoặc `account_key` vào bảng `blocked_accounts`, loại bỏ ngay lập tức khỏi `GET /api/queue`.
   - Tuân thủ Luật 91/2025/QH15 (SongNhat là **Data Controller**, Team triển khai/hạ tầng tại Việt Nam là **Data Processor**, cấm chuyển dữ liệu xuyên biên giới trái phép) và Luật 134/2025/QH15 / QĐ 33/2026/QĐ-TTg (không thuộc nhóm AI rủi ro cao).

---

## 3. Hướng dẫn chạy trên máy cá nhân (Windows)

Mở PowerShell tại thư mục gốc dự án và sử dụng `py` (hoặc Python trong `.venv`):

```powershell
# 0. Cài đặt các thư viện phụ thuộc
py -m pip install -r requirements.txt

# 1. Sinh dữ liệu mẫu chuẩn và kiểm toán dữ liệu (US-03 / PLPT-11)
py scripts/generate_songnhat_data.py
py scripts/us_03_check.py

# 2. Chạy pipeline nạp CSV và gộp trùng
py -m src.ingest.csv_validator
py -m src.ingest.deduplicator

# 3. Chạy toàn bộ unit test
py -m pytest

# 4. Khởi động FastAPI server
py -m uvicorn src.api.main:app --reload
```

- Tài liệu API tương tác (Swagger UI): `http://127.0.0.1:8000/docs`
- API hàng đợi & chặn khẩn cấp: `http://127.0.0.1:8000/api/queue` | `POST /api/block` | `GET /api/health`
- Giao diện vận hành (`PLPT-17`): mở file [frontend/index.html](frontend/index.html) trên trình duyệt sau khi FastAPI server đã khởi động.
