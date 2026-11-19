# US-02 (`PLPT-10`): Bộ tiêu chí Chốt khách (Rubric) & Quy ước 3 Trạng thái Kết quả

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
---

## 1. Bộ tiêu chí xác nhận Đạt chuẩn (Qualification Rubric — 3 điều kiện bắt buộc)
Một yêu cầu hỏi mua hàng chỉ được ghi nhận là **Đạt chuẩn (`POSITIVE`)** khi có **đủ cả 3 bằng chứng** được nhân viên Sales/Quản lý xác nhận:

1. **Xác nhận nhu cầu công nghiệp thực tế (`business_need_confirmed = Yes`):** Khách hàng doanh nghiệp có nhu cầu mua mới hoặc thay thế thiết bị phục vụ sản xuất thực tế, không phải hỏi giá tham khảo chung chung.
2. **Đúng danh mục sản phẩm SongNhat (`product_fit_confirmed = Yes`):** Nhu cầu khớp với 3 nhóm sản phẩm công nghiệp cốt lõi của SongNhat (`Motor_DongCo` — Động cơ điện, `Drive_BienTan` — Biến tần, `Transmission_HopSo` — Hộp số truyền động) và đáp ứng thông số kỹ thuật.
3. **Chốt được bước thương mại tiếp theo (`agreed_next_step_confirmed = Yes`):** Hai bên đã thống nhất lịch hẹn khảo sát, gửi báo giá chính thức hoặc lịch trao đổi kỹ thuật tiếp theo.

* **Quy tắc loại trừ nghiêm ngặt:** Việc nhân viên bấm chuyển trạng thái trên CRM (ví dụ từ `New` sang `Contacted` hoặc `Quoted`) **mà không có đủ 3 bằng chứng trên bị từ chối**, không được tính là nhãn `POSITIVE`.

---

## 2. Quy ước 3 Trạng thái Kết quả sau 60 ngày (`maturity_state`)

| Trạng thái | Điều kiện ghi nhận | Quy tắc bắt buộc |
| :--- | :--- | :--- |
| **`POSITIVE` (Đạt)** | Đạt đủ 3 tiêu chí Rubric trong vòng **`≤ 60 ngày`** kể từ `t0_time`. | Phải gắn kèm `event_time` và bằng chứng xác nhận (`has_verified_rubric = True`). |
| **`NEGATIVE` (Không đạt)** | Đã trôi qua **đủ 60 ngày** quan sát đầy đủ (`observer_coverage = Complete`) và **không có** sự kiện đạt Rubric nào. | Chỉ gán `NEGATIVE` khi đã đủ 60 ngày hoàn chỉnh. |
| **`Unknown` (Chưa rõ)** | Hồ sơ mới **chưa đủ 60 ngày** kể từ `t0_time` hoặc thiếu dữ liệu quan sát. | **Cấm tuyệt đối** gán nhầm thành `NEGATIVE` hay "Trượt/Mất khách". |

* **Tách biệt trạng thái vận hành (`workflow_state`):** Các trạng thái làm việc hàng ngày (`open`, `qualified`, `withdrawn`, `merged`, `restricted`) được lưu tách biệt hoàn toàn với nhãn kết quả 60 ngày để không làm sai lệch dữ liệu huấn luyện.

---

## 3. Công thức chấm điểm Rules Baseline (Thang 100 điểm — không dùng LLM)
Các hồ sơ hợp lệ được chấm điểm minh bạch theo 4 tiêu chí sau (tổng tối đa 100 điểm, hiển thị tối đa **3 lý do đóng góp cao nhất**, không hiển thị dưới dạng `%` xác suất thắng đơn):

| Tiêu chí | Điểm tối đa | Quy tắc tính điểm |
| :--- | :---: | :--- |
| **1. Đúng nhóm sản phẩm (`product_fit`)** | **30 điểm** | +30đ nếu thuộc `Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`. |
| **2. Đủ thông số kỹ thuật (`spec_completeness`)** | **25 điểm** | Chuẩn `Full`: +25đ; `Partial`: +15đ; `Minimal`: +5đ (hỗ trợ fallback `Yes`→`Full`, `No`→`Minimal`). |
| **3. Tuổi hồ sơ từ `t0_time` (`enquiry_age`)** | **20 điểm** | ≥ 7 ngày: +20đ; ≥ 3 ngày: +15đ; ≥ 1 ngày: +10đ; < 1 ngày: +5đ. |
| **4. Độ gấp hạn hợp đồng (`sla_urgency`)** | **25 điểm** | Còn ≤ 1 ngày: +25đ; ≤ 3 ngày: +20đ; ≤ 7 ngày: +12đ; > 7 ngày: +5đ. |
| **Luật ưu tiên `TIER_0`** | **Lên đầu bảng** | Áp dụng cho hồ sơ thay thế khẩn cấp hoặc còn `days_left <= 7` ngày tới hạn hợp đồng tính từ `now` (bao gồm cả đơn quá hạn `days_left < 0`; chỉ tính với đơn chưa `Closed`/`withdrawn`). |

* **Mã khóa dữ liệu cũ:** Hệ thống sử dụng duy nhất mã trạng thái chuẩn `HOLD_STALE_DATA` khi tuổi dữ liệu vượt quá 24 giờ hoặc không xác minh được `extraction_time`.
