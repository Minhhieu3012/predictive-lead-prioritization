# US-01 (`PLPT-9`): Định nghĩa chuẩn Thời điểm `t0`, Khung 60 ngày & Công suất `K`

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
**Người thực hiện:** Phan Minh Hiếu (Team 08) | **Ngày:** 9/11/2026 | **Trạng thái:** Hoàn chỉnh

---

## 1. Định nghĩa toán học & vận hành của Thời điểm `t0` (`t0_time`)
* **Khái niệm:** `t0_time` là mốc thời gian **đầu tiên** một yêu cầu hỏi mua hàng (Enquiry) thỏa mãn đầy đủ tiêu chí hợp lệ để đưa vào hàng đợi xem xét, **trước khi** nhân viên Sales thực hiện bất kỳ thao tác phân loại hay liên hệ thủ công nào.
* **Quy tắc khóa cứng (Anti-Leakage):**
  * Định dạng lưu trữ chuẩn: ISO 8601 theo giờ Việt Nam (`YYYY-MM-DDTHH:MM:SS+07:00`).
  * Tuyệt đối **cấm nhân viên tự sửa hoặc dời lùi `t0_time`** sau khi đã biết kết quả chốt đơn.
  * Mọi biến đầu vào (`product_family`, `spec_completeness`, lịch sử mua hàng trước đó) chỉ được phép lấy giá trị có `feature_event_time <= t0_time` và `feature_available_at <= t0_time`. Các báo giá hoặc ghi chú phát sinh sau `t0_time` bị loại hoàn toàn khỏi dữ liệu tính điểm.

## 2. Khung thời gian quan sát mục tiêu (Prediction Horizon)
* **Thời hạn cố định:** Đúng **60 ngày dương lịch** tính từ `t0_time` (`t0_time` đến `t0_time + 60 ngày`).
* **Quy ước xử lý:**
  * Không rút ngắn khung 60 ngày và không tự ý thay đổi mốc thời gian cho từng nhóm khách hàng riêng lẻ.
  * Mọi yêu cầu chưa đi qua đủ 60 ngày và chưa có sự kiện chốt đạt chuẩn đều giữ nguyên trạng thái **`Unknown` (Chưa trưởng thành)**.

## 3. Công suất xử lý hàng ngày `K` & Tỷ lệ khám phá ngẫu nhiên (10%)
* **Quy mô đội ngũ phục vụ:** **20 nhân viên Sales** (giới hạn **5 phiên đăng nhập đồng thời**), phục vụ 1 phân khúc thiết bị công nghiệp của SongNhat (Động cơ điện, Biến tần, Hộp số truyền động).
* **Công suất `K` tham chiếu:** Trung bình **25 hồ sơ/ngày làm việc** cho toàn đội (tương đương ~500 hồ sơ mới/tháng), đảm bảo thời gian phân loại giảm từ **6 phút/hồ sơ** xuống **3 phút/hồ sơ** (tiết kiệm 25 giờ/tháng).
* **Cơ chế phân bổ hàng đợi (`K` slots/ngày):**
  1. **Nhóm ưu tiên bắt buộc (`TIER_0`):** Các yêu cầu thay thế thiết bị khẩn cấp (`is_urgent_replacement = True`) và hồ sơ sát hạn cam kết hợp đồng (`contract_due_date`) được đẩy thẳng lên đầu hàng đợi và **không bao giờ bị bốc ngẫu nhiên**.
  2. **Nhóm theo điểm quy tắc (90% suất còn lại):** Sắp xếp theo `rule_score` giảm dần, trùng điểm xếp theo thứ tự đến trước phục vụ trước (FIFO theo `t0_time`).
  3. **Suất khám phá ngẫu nhiên (10% suất tùy chọn):** Dành đúng **10%** số slot phân bổ cho các hồ sơ đủ điều kiện nhưng có điểm xếp hạng thấp hơn để tránh bỏ sót khách hàng tiềm năng và giữ dữ liệu đánh giá khách quan.
  4. **Cảnh báo quá hạn (2 ngày làm việc):** Bất kỳ hồ sơ hợp lệ nào quá **2 ngày làm việc** kể từ `t0_time` chưa được xem xét sẽ tự động gắn cờ quá hạn báo lên cấp Quản lý (Supervisor) — tuyệt đối không tự động xóa hay ẩn hồ sơ.
