# US-02 (`PLPT-10`): Bộ tiêu chí Chốt khách (Rubric) & Quy ước 3 Trạng thái Kết quả

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
**Người thực hiện:** Phan Minh Hiếu (Team 08) | **Ngày:** 9/11/2026 | **Trạng thái:** Hoàn chỉnh

---

## 1. Bộ tiêu chí xác nhận Đạt chuẩn (Qualification Rubric — 3 điều kiện bắt buộc)

Một yêu cầu hỏi mua hàng chỉ được ghi nhận là **Đạt chuẩn (`POSITIVE`)** khi có **đủ cả 3 bằng chứng** được nhân viên Sales/Quản lý xác nhận:

1. **Xác nhận nhu cầu công nghiệp thực tế (`business_need_confirmed = Yes`):** Khách hàng doanh nghiệp có nhu cầu mua mới hoặc thay thế thiết bị phục vụ sản xuất thực tế, không phải hỏi giá tham khảo chung chung.
2. **Đúng danh mục sản phẩm SongNhat (`product_fit_confirmed = Yes`):** Nhu cầu khớp với 3 nhóm sản phẩm công nghiệp cốt lõi của SongNhat (`Motor_DongCo` — Động cơ điện, `Drive_BienTan` — Biến tần, `Transmission_HopSo` — Hộp số truyền động) và đáp ứng thông số kỹ thuật.
3. **Chốt được bước thương mại tiếp theo (`agreed_next_step_confirmed = Yes`):** Hai bên đã thống nhất lịch hẹn khảo sát, gửi báo giá chính thức hoặc lịch trao đổi kỹ thuật tiếp theo.

- **Quy tắc loại trừ nghiêm ngặt:** Việc nhân viên bấm chuyển trạng thái trên CRM (ví dụ từ `New` sang `Contacted` hoặc `Quoted`) **mà không có đủ 3 bằng chứng trên bị từ chối**, không được tính là nhãn `POSITIVE`. Bộ tiêu chí Rubric và phạm vi phân khúc áp dụng phải được ký duyệt cùng **Sales Owner** và kiểm tra tính phù hợp tại cổng **G0** (DAT 03).

---

## 2. Quy ước 3 Trạng thái Kết quả sau 60 ngày (`maturity_state`)

| Trạng thái                 | Điều kiện ghi nhận                                                                                                  | Quy tắc bắt buộc                                                                 |
| :------------------------- | :------------------------------------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------- |
| **`POSITIVE` (Đạt)**       | Đạt đủ 3 tiêu chí Rubric trong vòng **`≤ 60 ngày`** kể từ `t0_time`.                                                | Phải gắn kèm `event_time` và bằng chứng xác nhận (`has_verified_rubric = True`). |
| **`NEGATIVE` (Không đạt)** | Đã trôi qua **đủ 60 ngày** quan sát đầy đủ (`observer_coverage = Complete`) và **không có** sự kiện đạt Rubric nào. | Chỉ gán `NEGATIVE` khi đã đủ 60 ngày hoàn chỉnh.                                 |
| **`Unknown` (Chưa rõ)**    | Hồ sơ mới **chưa đủ 60 ngày** kể từ `t0_time` hoặc thiếu dữ liệu quan sát.                                          | **Cấm tuyệt đối** gán nhầm thành `NEGATIVE` hay "Trượt/Mất khách".               |

- **Tách biệt trạng thái vận hành (`workflow_state`):** Các trạng thái làm việc hàng ngày (`open`, `qualified`, `withdrawn`, `merged`, `restricted`, `invalid`) được lưu tách biệt hoàn toàn với nhãn kết quả 60 ngày (`maturity_state`) để không làm sai lệch dữ liệu huấn luyện (DAT 05).
- **Quy tắc xử lý ngoại lệ vòng đời & chỉnh sửa nhãn (DAT 05, DAT 07, MOD 08, Annex C):**
  - **Hồ sơ đóng sớm (_Early Closure_):** Nếu hồ sơ chuyển `workflow_state = withdrawn` trước 60 ngày (khách hủy hoặc ngừng nhu cầu, không phải lỗi dữ liệu), vẫn giữ nguyên `t0_time` ban đầu. Khi hết 60 ngày với điều kiện quan sát đầy đủ (`observer_coverage = Complete`) và không có sự kiện đạt Rubric nào trước khi đóng, nhãn được chốt là `NEGATIVE`; nếu thiếu điều kiện quan sát hoặc hồ sơ thuộc loại `invalid`, giữ nguyên `Unknown` / loại theo quy tắc kiểm toán.
  - **Hồ sơ mở lại (_Reopened Enquiries_):** Nếu hồ sơ mở lại cho cùng một nhu cầu mua hàng ban đầu, giữ nguyên khóa định danh (`enquiry_id`) và `t0_time` gốc để tránh nhân đôi vị trí hàng đợi hoặc tính trùng kết quả. Chỉ tạo `Enquiry` mới với `t0_time` mới khi xác minh đây là nhu cầu mua hàng hoặc dự án hoàn toàn tách biệt (có xác nhận của Data Steward).
  - **Chỉnh sửa nhãn (_Label Corrections_):** Mọi cập nhật hoặc đính chính bằng chứng/nhãn phải tạo bản ghi thay đổi có phiên bản (`versioned snapshot & label change`) kèm dấu thời gian và lý do, **tuyệt đối không ghi đè âm thầm điểm số hoặc lịch sử quá khứ**. Việc tính lại điểm (nếu có) chỉ thực hiện qua quy trình chấm điểm lại có phiên bản (_versioned rescore_), đồng thời rà soát xem sai lệch nhãn có buộc phải huấn luyện lại, giới hạn hay ngưng sử dụng mô hình liên quan hay không; từ chối các bản cập nhật đến muộn có nguy cơ ghi đè trạng thái vận hành mới hơn (DAT 07).

---

## 3. Công thức chấm điểm Rules Baseline (Thang 100 điểm — không dùng LLM)

Các hồ sơ hợp lệ được chấm điểm minh bạch theo 4 tiêu chí sau (tổng tối đa 100 điểm). Đầu ra phải được gắn nhãn rõ là **điểm quy tắc (`rule_score`)** — không phải hiệu năng dự đoán và tuyệt đối không hiển thị dưới dạng `%` xác suất thắng đơn — đồng thời hiển thị tối đa **3 lý do đóng góp cao nhất** kèm giá trị hợp lệ và dấu thời gian của trường dữ liệu nguồn (MOD 01, MOD 05, MOD 06).

| Tiêu chí                                          |   Điểm tối đa    | Quy tắc tính điểm                                                                                                                                                                       |
| :------------------------------------------------ | :--------------: | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Đúng nhóm sản phẩm (`product_fit`)**         |   **30 điểm**    | +30đ nếu thuộc `Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`.                                                                                                                   |
| **2. Đủ thông số kỹ thuật (`spec_completeness`)** |   **25 điểm**    | Chuẩn `Full`: +25đ; `Partial`: +15đ; `Minimal`: +5đ (hỗ trợ fallback `Yes`→`Full`, `No`→`Minimal`).                                                                                     |
| **3. Tuổi hồ sơ từ `t0_time` (`enquiry_age`)**    |   **20 điểm**    | ≥ 7 ngày: +20đ; ≥ 3 ngày: +15đ; ≥ 1 ngày: +10đ; < 1 ngày: +5đ.                                                                                                                          |
| **4. Độ gấp hạn hợp đồng (`sla_urgency`)**        |   **25 điểm**    | Còn ≤ 1 ngày: +25đ; ≤ 3 ngày: +20đ; ≤ 7 ngày: +12đ; > 7 ngày: +5đ.                                                                                                                      |
| **Luật ưu tiên `TIER_0`**                         | **Lên đầu bảng** | Áp dụng cho hồ sơ thay thế khẩn cấp hoặc còn `days_left <= 7` ngày tới hạn hợp đồng tính từ `now` (bao gồm cả đơn quá hạn `days_left < 0`; chỉ tính với đơn chưa `Closed`/`withdrawn`). |

- **Xử lý thiếu dữ liệu (Missing Handling — MOD 06, Annex B):** Nếu thiếu `spec_completeness` (hoặc giá trị không hợp lệ) thì gán `+0đ` và hiển thị rõ thông tin còn thiếu để Sales kiểm tra; nếu không có `contract_due_date` (`NULL`) thì `sla_urgency` nhận `+0đ` (không xếp nhầm vào mức `> 7 ngày: +5đ`).
- **Mã khóa dữ liệu cũ:** Hệ thống sử dụng duy nhất mã trạng thái chuẩn `HOLD_STALE_DATA` khi tuổi dữ liệu vượt quá 24 giờ hoặc không xác minh được `extraction_time`.

