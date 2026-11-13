# Cấu trúc 2 file dữ liệu đầu vào (SongNhat AI-02)

Tài liệu này giải thích ý nghĩa từng cột trong 2 file CSV chuẩn UTF-8 đặt tại thư mục `data/raw/`.

---

## 1. File `data/raw/enquiries_snapshot.csv`
Lưu thông tin của từng yêu cầu hỏi mua thiết bị công nghiệp tại thời điểm tiếp nhận (`t0_time`).

| Tên cột | Kiểu dữ liệu | Ý nghĩa dễ hiểu |
|---|---|---|
| `enquiry_id` | Chuỗi (Khóa chính) | Mã duy nhất của từng lượt hỏi mua (ví dụ: `SN-0001`). |
| `account_key` | Chuỗi | Mã định danh khách hàng doanh nghiệp (không dùng tên riêng). |
| `cohort` | Chuỗi | Nhóm dữ liệu: `TRAINING_CANDIDATE` (huấn luyện), `SEALED_LATER_TEST` (kiểm thử sau), `OPEN_24H` (mới trong 24h), `QUARANTINE_TEST` (dòng lỗi mẫu). |
| `source` | Chuỗi | Nguồn gốc khách hàng biết đến công ty (`Website`, `Direct Sales`, `Referral`, `Trade Fair`). |
| `t0_time` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm chốt mốc tính điểm theo giờ Việt Nam. Mọi dữ liệu sau mốc này đều không được dùng để chấm điểm. |
| `extraction_time` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm trích xuất file dữ liệu. Dùng để kiểm tra dữ liệu có bị cũ quá 24 giờ hay không. |
| `product_family` | Chuỗi | Nhóm thiết bị hỏi mua. Chỉ nhận 3 giá trị: `Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`. |
| `spec_completeness` | Chuỗi (`Yes`/`No`) | Khách đã cung cấp đủ thông số kỹ thuật hay chưa. |
| `is_urgent_replacement` | Chuỗi (`Urgent`/`Normal`) | Khách có đang cần thay thế thiết bị gấp hay không. |
| `contract_due_date` | Ngày (`YYYY-MM-DD`) | Hạn chót giao hàng/hợp đồng dự kiến của khách. |
| `workflow_state` | Chuỗi | Trạng thái xử lý hiện tại trên hệ thống (`New`, `Contacted`, `Under_Review`, `Quoted`, `Closed`). Chỉ dùng để tham khảo khi xuất file, không dùng để tính điểm. |
| `industry_at_t0` | Chuỗi | Ngành nghề của khách hàng tại thời điểm `t0_time`. |
| `enquiry_channel_at_t0` | Chuỗi | Kênh khách hàng gửi yêu cầu (`Website`, `Email`, `Phone`, `Trade Fair`, `Referral`). |
| `region_at_t0` | Chuỗi | Khu vực địa lý của khách hàng (`North`, `Central`, `South`). |
| `account_age_days_at_t0` | Số nguyên | Số ngày khách hàng đã có tài khoản trên hệ thống tính đến `t0_time`. |
| `prior_purchase_count_at_t0` | Số nguyên | Số lần khách hàng đã mua hàng trước thời điểm `t0_time`. |
| `estimated_value_band_at_t0` | Chuỗi | Mức giá trị đơn hàng ước tính tại `t0_time`. |
| `feature_event_time` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm phát sinh thông tin đầu vào (bắt buộc `<= t0_time`). |
| `feature_available_at` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm hệ thống ghi nhận được thông tin đó (bắt buộc `<= t0_time`). |
| `contact_phone_DO_NOT_USE` | Chuỗi | Số điện thoại liên hệ. **Cấm tuyệt đối** đưa vào biến tính điểm để bảo mật thông tin cá nhân. |
| `latest_quote_value_post_t0_DO_NOT_USE` | Số thực | Giá trị báo giá phát sinh *sau* `t0_time`. **Cấm tuyệt đối** dùng để tính điểm vì làm lộ kết quả tương lai. |
| `latest_quote_available_at_DO_NOT_USE` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm có báo giá *sau* `t0_time`. **Cấm tuyệt đối** dùng để tính điểm. |

---

## 2. File `data/raw/outcome_events.csv`
Lưu kết quả theo dõi trong vòng 60 ngày kể từ `t0_time` để biết khách hàng có chuyển đổi thành cơ hội bán hàng thực tế hay không.

| Tên cột | Kiểu dữ liệu | Ý nghĩa dễ hiểu |
|---|---|---|
| `enquiry_id` | Chuỗi (Khóa ngoại) | Mã lượt hỏi mua, khớp 1-1 với file `enquiries_snapshot.csv`. |
| `event_type` | Chuỗi | Loại sự kiện kết quả (`QUALIFICATION_CONFIRMED`, `60_DAY_MATURITY_NO_QUALIFICATION`, hoặc `PENDING_OBSERVATION` nếu chưa đủ 60 ngày). |
| `event_time` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm xảy ra sự kiện xác nhận kết quả. |
| `recorded_at` | Ngày giờ ISO 8601 (`+07:00`) | Thời điểm nhân viên ghi nhận kết quả lên hệ thống. |
| `qualification_evidence` | Chuỗi | Mô tả bằng chứng xác nhận nhu cầu đủ điều kiện. |
| `business_need_confirmed` | Chuỗi (`Yes`/`No`/`Unknown`) | Đã xác nhận đúng nhu cầu kinh doanh của khách chưa. |
| `product_fit_confirmed` | Chuỗi (`Yes`/`No`/`Unknown`) | Thiết bị của công ty có phù hợp với yêu cầu của khách chưa. |
| `agreed_next_step_confirmed` | Chuỗi (`Yes`/`No`/`Unknown`) | Khách và Sales đã chốt bước làm việc tiếp theo chưa. |
| `observer_coverage` | Chuỗi | Mức độ theo dõi đủ 60 ngày (`Complete` hoặc `In_Progress`). |
| `observation_window_days` | Số nguyên | Số ngày đã theo dõi (đủ chuẩn là `60`). |
| `maturity_state` | Chuỗi | Trạng thái đủ 60 ngày (`MATURE`) hay chưa đủ (`IMMATURE`). |
| `label` | Chuỗi | Nhãn kết quả cuối cùng: `POSITIVE` (chuyển đổi), `NEGATIVE` (không chuyển đổi), `Unknown` (chưa đủ 60 ngày). |
| `correction_status` | Chuỗi | Trạng thái chỉnh sửa nhãn nếu có đính chính sau này. |
| `cohort` | Chuỗi | Nhóm dữ liệu, khớp hoàn toàn với cột `cohort` bên file `enquiries_snapshot.csv`. |