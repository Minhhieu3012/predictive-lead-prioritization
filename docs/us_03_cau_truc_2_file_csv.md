# US-03: Cấu trúc chuẩn 2 file CSV đầu vào

Tài liệu này khóa cứng cấu trúc dữ liệu đầu vào cho pipeline **SongNhat AI-02**, được sinh từ script `scripts/generate_songnhat_data.py` và kiểm tra bởi `scripts/us_03_check.py`.

---

## 1. File `data/raw/enquiries_snapshot.csv` (**22 cột**, UTF-8)

File chứa ảnh chụp các yêu cầu báo giá tại thời điểm `t0_time`. Khi nạp vào SQLite (`raw_enquiries` và `clean_enquiries`), hệ thống sẽ tự động loại bỏ 3 cột đánh dấu `*_DO_NOT_USE` để còn **19 cột** phục vụ chấm điểm.

| STT | Tên cột | Kiểu dữ liệu | Dùng trong DB/Scoring | Mô tả |
| :-: | :--- | :--- | :---: | :--- |
| 1 | `enquiry_id` | `TEXT` (PK) | Có | Mã định danh yêu cầu báo giá (`SN-0001`...) |
| 2 | `account_key` | `TEXT` | Có | Mã khách hàng doanh nghiệp (`ACCT-...`) |
| 3 | `cohort` | `TEXT` | Có | Tập dữ liệu (`TRAINING_CANDIDATE`, `SEALED_LATER_TEST`, `OPEN_24H`) |
| 4 | `source` | `TEXT` | Có | Hệ thống nguồn tiếp nhận |
| 5 | `t0_time` | `TEXT` (ISO-8601) | Có | Thời điểm gốc phát sinh yêu cầu (bắt buộc hợp lệ) |
| 6 | `extraction_time` | `TEXT` (ISO-8601) | Có | Thời điểm trích xuất dữ liệu (dùng kiểm tra freshness 24h) |
| 7 | `product_family` | `TEXT` | Có | Nhóm sản phẩm (`Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`) |
| 8 | `spec_completeness` | `TEXT` | Có | Mức độ đầy đủ thông số kỹ thuật (`Full`, `Partial`, `Minimal`) |
| 9 | `is_urgent_replacement` | `TEXT` | Có | Cờ thay thế khẩn cấp (`Urgent` / `Normal`) |
| 10 | `contract_due_date` | `TEXT` (`YYYY-MM-DD`) | Có | Hạn giao hàng/hợp đồng cam kết (tên chuẩn thống nhất) |
| 11 | `workflow_state` | `TEXT` | Có | Trạng thái xử lý (`New`, `Under_Review`, `Contacted`, `Quoted`, `Closed`) |
| 12 | `industry_at_t0` | `TEXT` | Có | Ngành nghề khách hàng tại `t0_time` |
| 13 | `enquiry_channel_at_t0` | `TEXT` | Có | Kênh tiếp nhận (`Direct_Sales`, `Email`, `Zalo`, `Website`...) |
| 14 | `region_at_t0` | `TEXT` | Có | Khu vực địa lý tại `t0_time` |
| 15 | `account_age_days_at_t0` | `INTEGER` | Có | Số ngày tuổi tài khoản khách hàng tại `t0_time` |
| 16 | `prior_purchase_count_at_t0` | `INTEGER` | Có | Số lần mua hàng trước thời điểm `t0_time` |
| 17 | `estimated_value_band_at_t0` | `TEXT` | Có | Nhóm giá trị ước tính (`High`, `Medium`, `Low`) |
| 18 | `feature_event_time` | `TEXT` (ISO-8601) | Có | Thời điểm phát sinh đặc trưng |
| 19 | `feature_available_at` | `TEXT` (ISO-8601) | Có | Thời điểm đặc trưng sẵn sàng (chống rò rỉ dữ liệu tương lai) |
| 20 | `contact_phone_DO_NOT_USE` | `TEXT` | **Bị loại** | Số điện thoại liên hệ (PII — bị cắt bỏ khi nạp DB) |
| 21 | `latest_quote_value_post_t0_DO_NOT_USE` | `TEXT` | **Bị loại** | Giá trị báo giá sau `t0` (gây data leakage — bị cắt bỏ) |
| 22 | `latest_quote_available_at_DO_NOT_USE` | `TEXT` | **Bị loại** | Thời điểm báo giá sau `t0` (bị cắt bỏ khi nạp DB) |

---

## 2. File `data/raw/outcome_events.csv` (**14 cột**, UTF-8)

File ghi nhận kết quả thẩm định cơ hội trong cửa sổ quan sát 60 ngày, được nạp nguyên vẹn **14 cột** vào bảng `raw_outcomes`.

| STT | Tên cột | Kiểu dữ liệu | Mô tả |
| :-: | :--- | :--- | :--- |
| 1 | `enquiry_id` | `TEXT` (PK) | Khóa liên kết với `enquiries_snapshot.csv` |
| 2 | `event_type` | `TEXT` | Loại sự kiện kết quả |
| 3 | `event_time` | `TEXT` (ISO-8601) | Thời điểm ghi nhận sự kiện |
| 4 | `recorded_at` | `TEXT` (ISO-8601) | Thời điểm lưu vào hệ thống |
| 5 | `qualification_evidence` | `TEXT` | Bằng chứng thẩm định cơ hội |
| 6 | `business_need_confirmed` | `TEXT` | Xác nhận nhu cầu kinh doanh |
| 7 | `product_fit_confirmed` | `TEXT` | Xác nhận phù hợp sản phẩm |
| 8 | `agreed_next_step_confirmed` | `TEXT` | Xác nhận bước tiếp theo với khách hàng |
| 9 | `observer_coverage` | `TEXT` | Độ phủ quan sát |
| 10 | `observation_window_days` | `INTEGER` | Cửa sổ quan sát (mặc định `60` ngày) |
| 11 | `maturity_state` | `TEXT` | Trạng thái trưởng thành nhãn (`MATURE` / `IMMATURE`) |
| 12 | `label` | `TEXT` | Nhãn kết quả (`POSITIVE`, `NEGATIVE`, `Unknown`) |
| 13 | `correction_status` | `TEXT` | Trạng thái hiệu chỉnh nhãn |
| 14 | `cohort` | `TEXT` | Tập dữ liệu tương ứng |

---

## 3. Quy tắc kiểm tra lỗi (Quarantine)

- Các dòng thiếu `t0_time` hoặc sai định dạng ISO-8601 (ví dụ `ERR-0001` đến `ERR-0010`) sẽ không được nạp vào `raw_enquiries` mà được tách riêng ra `data/quarantine/quarantine_rows.csv` kèm cột `quarantine_reason`.
