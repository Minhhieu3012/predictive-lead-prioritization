# PO Decisions — Daily Lead Workspace (AI-02, đối chiếu v16)

> **Mục đích:** Chốt các business rules cho Daily Lead Workspace của đội dev trên nền tảng v16 (`src/services/queue_service.py`, `src/api/main.py`, `src/security/rbac.py`, `src/config/vn_holidays.py`).
> **Vai trò:** Tài liệu đối chiếu nghiệp vụ cho PO/QA/Dev trên phạm vi Sprint 1–3.
> **Nguyên tắc:** Chỉ triển khai đúng User Story và AC1–AC4; không mở rộng thành CRM hoặc thêm tính năng ngoài phạm vi.

## 1. Capacity K & Xử lý quá tải (`CAPACITY_EXCEEDED`)

- `K = 25` (`DAILY_CAPACITY_K`, mặc định `25`) là tổng số lượt review mục tiêu tối đa mỗi ngày của Sales team, **bao gồm cả mandatory work**.
- Mandatory work được đưa vào queue trước discretionary work.
- Nếu số mandatory work `M > K`:
  - Vẫn giữ **toàn bộ `M` hồ sơ mandatory work** trong hàng đợi (không cắt bớt để ép hard cap K).
  - Ghi nhận `status = "CAPACITY_EXCEEDED"`, `is_overloaded = true`, và `capacity_exceeded_by = M - K`.
  - Không tạo discretionary slots và set `exploration_slots_allocated = 0` trong ngày đó.
  - Giữ nguyên `carry_out_tenths = carry_in_tenths` (không tiêu hao phần lẻ exploration).
- Nếu `M <= K`, số discretionary slots:

  `D = max(0, K - M)`

## 2. Mandatory work (`TIER_0` và `is_overdue_2bd`)

Một enquiry là mandatory nếu thỏa **ít nhất một** điều kiện:

1. `tier0_bypass = true`: `is_urgent_replacement = true` hoặc `contract_due_date` nằm trong vòng 0–7 ngày tính từ `t0_time` (theo `is_tier0_urgent` trong `src/rules/rules_engine.py`).
2. `is_overdue_2bd = true`: hồ sơ chưa hoàn tất review (`review_completed != 1` trong `staff_review_logs`) và số ngày làm việc Việt Nam từ `t0_time` đến thời điểm hiện tại (`count_vn_business_days` trong `src/config/vn_holidays.py`, có tính ngày lễ/Tết VN) vượt quá 2 ngày (`> 2`).

Mandatory work:
- Luôn được ưu tiên đưa vào queue trước các lead thông thường (`allocation_type = "TIER_0_MANDATORY"`).
- Không được đưa vào nhóm random exploration.
- Không bị loại khỏi queue do điểm thấp hoặc thứ hạng thấp.

## 3. Exploration allocation — 10% (Fractional Carry)

- Dành 10% discretionary review slots cho randomized exploration (`allocation_type = "EXPLORATION_10PCT"`).
- Exploration chỉ áp dụng trên các enquiry hợp lệ, đã được scoring, không thuộc mandatory work và không nằm trong quarantine.
- Nhóm exploration được lấy ngẫu nhiên có seed từ các eligible enquiries có thứ hạng thấp hơn nhóm `TOP_RANKED`.

### Quy tắc tích lũy phần lẻ (`carry_in_tenths` / `carry_out_tenths`)

Để tránh mất slot exploration khi `D = max(0, K - M)` nhỏ (ví dụ `D = 5` làm `int(0.1 * D) = 0`), hệ thống tích lũy theo phần mười (`tenths`):

- `quota_tenths = carry_in_tenths + D`
- `exploration_count = min(quota_tenths // 10, D)` (khi đủ pool ứng viên)
- `carry_out_tenths = quota_tenths - actual_explore * 10`

Ví dụ: `D = 5` mỗi ngày → `quota_tenths` lần lượt `5, 10, 5, 10...` → số slot exploration phát sinh lần lượt `0, 1, 0, 1...`, bảo đảm đúng 10% qua nhiều ngày liên tiếp.

## 4. Daily allocation snapshot (Idempotent theo ngày)

- Mỗi ngày làm việc theo múi giờ `Asia/Ho_Chi_Minh` (`business_date = YYYY-MM-DD`) chỉ tạo và khóa một snapshot trong bảng SQLite `daily_queue_allocations`.
- Lưu đầy đủ: `business_date`, `capacity_k`, `mandatory_count`, `discretionary_slots`, `ranked_slots`, `exploration_slots`, `exploration_seed`, `carry_in_tenths`, `carry_out_tenths`, `capacity_exceeded_by`, `status`, `selected_json`, `created_at`.
- Gọi `GET /api/v1/workspace/queue` nhiều lần trong cùng ngày sẽ trả lại đúng snapshot và `exploration_seed` đã chốt (idempotent, không random lại).

## 5. Staff Override (`staff_override_logs`) & Review (`staff_review_logs`)

- **Override (`POST /api/v1/workspace/override`)**:
  - Bắt buộc `override_reason_code` thuộc tập hợp lệ (`CUSTOMER_URGENT_CALLBACK`, `EXISTING_PROJECT_DEPENDENCY`, `TECHNICAL_SPEC_CLARIFICATION`, `OTHER_VERIFIED`) và `justification` không rỗng.
  - Ghi vào `staff_override_logs` (cách ly hoàn toàn khỏi `raw_outcomes` / `outcome_events`).
  - Khi có `OVERRIDE_PRIORITY`, hồ sơ được kéo lên `Active` (nếu nằm ngoài Top-K thì đẩy 1 hồ sơ không phải `TIER_0` điểm thấp nhất chưa review ra ngoài) và sắp xếp lại `final_work_order` ngay sau `TIER_0` (giữ nguyên `model_rank`, bật `rules_override = true`).
- **Review (`POST /api/v1/workspace/review`)**:
  - Bắt buộc xác minh `contact_permission_verified = true` nếu `contact_attempted = true` (lỗi `400` nếu thiếu).
- **Bảo vệ ghi dữ liệu**:
  - Hàng đợi ở trạng thái `HOLD_STALE_DATA` (`snapshot_age_hours > 24h` hoặc thiếu `extraction_time`) → trả HTTP `423`.
  - Hồ sơ không tồn tại → HTTP `404`; hồ sơ đã bị block (`blocked_accounts`) → HTTP `409`.

## 6. Rank, scoring mode, contributing factors & RBAC 5 vai trò

- Hiển thị **ordinal rank** (`model_rank`, `final_work_order`), tuyệt đối không diễn giải `rule_score` thành xác suất thắng đơn (kèm `disclaimer` và `non_causal_disclaimer`).
- `scoring_mode`: mặc định khóa `RULES_BASELINE` / `FALLBACK_RULES_BASELINE` cho tới khi đạt Gate G0 & phê duyệt Gate G1 mới bật `PREDICTIVE_ML`.
- Hiển thị tối đa 3 `contributing_factors` kèm `source_timestamp`.
- **RBAC 5 vai trò** (`src/security/rbac.py`, mặc định `RBAC_ENFORCE_AUTH=true`):
  - `SALES_READER`, `SUPERVISOR`, `DATA_STEWARD`, `MODEL_REVIEWER`, `ADMINISTRATOR`.
  - Thiếu thông tin xác thực hoặc phiên hết hạn (`X-Session-Expired: true`) → HTTP `401`.
  - Vai trò không đủ quyền thực hiện action (ví dụ `DATA_STEWARD` gọi override, `SALES_READER` gọi block) → HTTP `403`.

## 7. Phân tách 3 tab (`Active`, `Unscored`, `Quarantine`) & Tìm kiếm tiếng Việt

- **Tab `Active`** (`GET /api/v1/workspace/queue?view=active`):
  - Mặc định trả đúng `K` slot (hoặc toàn bộ `M` nếu `CAPACITY_EXCEEDED`).
  - Hỗ trợ `include_overflow=true` để hiển thị thêm các hồ sơ `Full`/`Partial` hạng `#K+1` trở đi với `allocation_type = "OVERFLOW_OUTSIDE_K"`.
- **Tab `Unscored`** (`view=unscored`):
  - Tách rạch ròi, không trùng hồ sơ với `Active`: chỉ giữ `product_family` ngoài 3 nhóm chuẩn (`Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`), `spec_completeness == ""` (trừ khi là `TIER_0`), hoặc `Minimal` chưa lọt Top-K.
- **Tab `Quarantine`** (`view=quarantine`):
  - Đọc từ `quarantine_rows.csv` và `quarantine_outcomes.csv` (không chứa PII nhạy cảm).
- **Tìm kiếm tiếng Việt không dấu**: hỗ trợ chuẩn hóa dấu tiếng Việt (bao gồm `đ`/`Đ`), ví dụ `"dong co"` khớp `"Motor_DongCo"`.

## 8. AC4 — Concurrency và latency

- Kiểm thử trên 5 concurrent active sessions với tối thiểu 100 requests được xác thực (`test_workspace_all_5_roles_and_100_req_5_sessions_p95`), bảo đảm `p95 <= 3.0s`.
