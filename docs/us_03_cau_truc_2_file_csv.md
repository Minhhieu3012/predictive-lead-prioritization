# US-03 (`PLPT-11`): Cấu trúc chuẩn 2 file CSV đầu vào

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
**Người thực hiện:** Nguyễn Tuấn (`PLPT-11`) | **Ngày:** 09/11/2026 | **Trạng thái:** Hoàn chỉnh

Tài liệu này khóa cứng cấu trúc dữ liệu đầu vào cho pipeline **SongNhat AI-02**, được sinh từ script `scripts/generate_songnhat_data.py` và kiểm tra bởi `scripts/us_03_check.py`.

---

## 1. File `data/raw/enquiries_snapshot.csv` (**22 cột**, UTF-8)

File chứa ảnh chụp các yêu cầu báo giá tại thời điểm `t0_time`. Khi nạp vào SQLite (`raw_enquiries` và `clean_enquiries`), hệ thống sẽ tự động loại bỏ 3 cột đánh dấu `*_DO_NOT_USE` để còn **19 cột** phục vụ chấm điểm.

| STT | Tên cột | Kiểu dữ liệu | Dùng trong DB/Scoring | Mô tả | Mục đích hợp pháp (`Lawful Purpose` — DAT 06) | Xử lý giá trị thiếu (`Missing Handling` — DAT 06) |
| :-: | :--- | :--- | :---: | :--- | :--- | :--- |
| 1 | `enquiry_id` | `TEXT` (PK) | Có | Mã định danh yêu cầu báo giá (`SN-0001`...) | Định danh ẩn danh đơn hỏi mua phục vụ nối bảng, gộp trùng và kiểm toán (`DAT 01`); **cấm dùng làm predictor**. | Bắt buộc có; nếu thiếu/rỗng → đưa vào `quarantine_rows.csv` (`Thieu enquiry_id`). |
| 2 | `account_key` | `TEXT` | Có | Mã khách hàng doanh nghiệp (`ACCT-...`) | Khóa định danh doanh nghiệp ẩn danh phục vụ gộp trùng đa kênh (`DAT 01`, `MOD 04`); **cấm dùng trực tiếp làm predictor**. | Bắt buộc có; nếu thiếu/rỗng → đưa vào `quarantine_rows.csv` (`Thieu account_key`). |
| 3 | `cohort` | `TEXT` | Có | Tập dữ liệu (`TRAINING_CANDIDATE`, `SEALED_LATER_TEST`, `OPEN_24H`) | Phân tách các tập dữ liệu theo thời gian phục vụ kiểm định độc lập (`MOD 03`). | Nếu thiếu → lưu chuỗi rỗng, loại khỏi các phép kiểm định phân tách `cohort`. |
| 4 | `source` | `TEXT` | Có | Hệ thống nguồn tiếp nhận | Đối soát chất lượng nguồn nhập và theo dõi thiên lệch theo nguồn (`DAT 07`, `DAT 08`). | Bù đắp từ bản ghi trùng hoặc tham chiếu `enquiry_channel_at_t0`; nếu thiếu → giữ rỗng. |
| 5 | `t0_time` | `TEXT` (ISO-8601) | Có | Thời điểm gốc phát sinh yêu cầu (bắt buộc hợp lệ) | Khóa mốc thời gian đủ điều kiện trước phân loại thủ công để chống rò rỉ tương lai (`DAT 02`). | Bắt buộc hợp lệ ISO-8601; nếu thiếu hoặc sai định dạng → đưa vào `quarantine_rows.csv`. |
| 6 | `extraction_time` | `TEXT` (ISO-8601) | Có | Thời điểm trích xuất dữ liệu (dùng kiểm tra freshness 24h) | Kiểm soát độ tươi dữ liệu nguồn (`<= 24 giờ`) và kích hoạt khóa `HELD_STALE_DATA` (`USR 07`). | Bắt buộc hợp lệ ISO-8601; nếu thiếu hoặc sai định dạng → đưa vào `quarantine_rows.csv`. |
| 7 | `product_family` | `TEXT` | Có | Nhóm sản phẩm (`Motor_DongCo`, `Drive_BienTan`, `Transmission_HopSo`) | Xác định khớp danh mục sản phẩm cốt lõi SongNhat phục vụ Rules/ML (`DAT 06`, `MOD 01`). | Bắt buộc thuộc 3 mã chuẩn; nếu thiếu hoặc sai danh mục → đưa vào `quarantine_rows.csv`. |
| 8 | `spec_completeness` | `TEXT` | Có | Mức độ đầy đủ thông số kỹ thuật (`Full`, `Partial`, `Minimal`) | Đánh giá độ đầy đủ thông số kỹ thuật tại `t0_time` phục vụ xếp hạng ưu tiên (`DAT 06`, `MOD 01`). | Bù đắp từ bản ghi trùng đa kênh; nếu vẫn thiếu → xử lý như `Minimal` (`+0 điểm`). |
| 9 | `is_urgent_replacement` | `TEXT` | Có | Cờ thay thế khẩn cấp (`Urgent` / `Normal`) | Bảo toàn quy trình xử lý khẩn cấp (`TIER_0`) độc lập với điểm số (`MOD 07`, `USR 02`). | Bù đắp từ bản ghi trùng (ưu tiên giữ `Urgent`); nếu thiếu → mặc định `Normal`. |
| 10 | `contract_due_date` | `TEXT` (`YYYY-MM-DD`) | Có | Hạn giao hàng/hợp đồng cam kết (tên chuẩn thống nhất) | Bảo vệ cam kết thời hạn hợp đồng (`SLA` / `TIER_0`) trong hàng đợi (`MOD 07`). | Bù đắp từ bản ghi trùng; nếu thiếu hoặc sai định dạng → bỏ qua cộng điểm/cảnh báo hạn, không tự suy diễn. |
| 11 | `workflow_state` | `TEXT` | Có | Trạng thái xử lý (`New`, `Under_Review`, `Contacted`, `Quoted`, `Closed`) | Theo dõi trạng thái vận hành tách biệt hoàn toàn với nhãn huấn luyện (`DAT 05`). | Bù đắp từ bản ghi trùng; nếu thiếu → coi như chưa đóng, không dùng làm predictor. |
| 12 | `industry_at_t0` | `TEXT` | Có | Ngành nghề khách hàng tại `t0_time` | Phân tích bối cảnh B2B và độ phủ phân khúc khách hàng tại `t0_time` (`DAT 08`, `SEC 05`). | Bù đắp từ bản ghi trùng; nếu vẫn thiếu → giữ rỗng/`Unknown`, không suy diễn sau `t0_time`. |
| 13 | `enquiry_channel_at_t0` | `TEXT` | Có | Kênh tiếp nhận (`Direct_Sales`, `Email`, `Zalo`, `Website`...) | Theo dõi kênh tiếp nhận hợp lệ tại `t0_time` và kiểm tra sai lệch chọn mẫu (`DAT 08`). | Bù đắp từ bản ghi trùng hoặc tham chiếu `source`; nếu thiếu → giữ rỗng/`Unknown`. |
| 14 | `region_at_t0` | `TEXT` | Có | Khu vực địa lý tại `t0_time` | Giám sát độ phủ theo khu vực B2B và rà soát proxy loại trừ (`SEC 05`, `DAT 08`). | Bù đắp từ bản ghi trùng; nếu thiếu → giữ rỗng/`Unknown`. |
| 15 | `account_age_days_at_t0` | `INTEGER` | Có | Số ngày tuổi tài khoản khách hàng tại `t0_time` | Tổng hợp lịch sử khách hàng doanh nghiệp trước `t0_time` (`MOD 04`). | Bù đắp từ bản ghi trùng; nếu thiếu → lưu `NULL` (`None`) trong SQLite, tách nhóm phân tích riêng (`Annex B`). |
| 16 | `prior_purchase_count_at_t0` | `INTEGER` | Có | Số lần mua hàng trước thời điểm `t0_time` | Đánh giá tần suất hợp tác trước `t0_time` (`MOD 04`), không suy diễn nhu cầu thấp cho khách mới. | Bù đắp từ bản ghi trùng; nếu thiếu → lưu `NULL` (`None`) trong SQLite, báo cáo tách biệt nhóm thiếu lịch sử. |
| 17 | `estimated_value_band_at_t0` | `TEXT` | Có | Nhóm giá trị ước tính (`High`, `Medium`, `Low`) | Phân tầng quy mô giá trị đơn hàng ước tính tại `t0_time` phục vụ phân tích B2B. | Bù đắp từ bản ghi trùng; nếu thiếu → giữ rỗng/`Unknown`. |
| 18 | `feature_event_time` | `TEXT` (ISO-8601) | Có | Thời điểm phát sinh đặc trưng | Chứng minh đặc trưng phát sinh `<= t0_time` phục vụ kiểm toán chống rò rỉ (`DAT 02`). | Nếu thiếu hoặc `> t0_time` → không đạt kiểm toán `AC3`, loại khỏi tập huấn luyện. |
| 19 | `feature_available_at` | `TEXT` (ISO-8601) | Có | Thời điểm đặc trưng sẵn sàng (chống rò rỉ dữ liệu tương lai) | Chứng minh dữ liệu đã khả dụng trong hệ thống tại `<= t0_time` (`DAT 02`, `MOD 03`). | Nếu thiếu hoặc `> t0_time` → không đạt kiểm toán `AC3`, loại khỏi tập huấn luyện. |
| 20 | `contact_phone_DO_NOT_USE` | `TEXT` | **Bị loại** | Số điện thoại liên hệ (PII — bị cắt bỏ khi nạp DB) | Không có mục đích hợp pháp cho chấm điểm/ML; là PII bắt buộc loại bỏ (`SEC 02`, Luật 91/2025/QH15). | Bỏ qua và tự động cắt bỏ hoàn toàn khi nạp SQLite. |
| 21 | `latest_quote_value_post_t0_DO_NOT_USE` | `TEXT` | **Bị loại** | Giá trị báo giá sau `t0` (gây data leakage — bị cắt bỏ) | Không hợp lệ cho dự báo vì là thông tin phát sinh sau `t0_time` gây rò rỉ dữ liệu (`DAT 02`). | Bỏ qua và tự động cắt bỏ hoàn toàn khi nạp SQLite. |
| 22 | `latest_quote_available_at_DO_NOT_USE` | `TEXT` | **Bị loại** | Thời điểm báo giá sau `t0` (bị cắt bỏ khi nạp DB) | Không hợp lệ cho dự báo vì là dấu thời gian báo giá sau `t0_time` gây rò rỉ (`DAT 02`). | Bỏ qua và tự động cắt bỏ hoàn toàn khi nạp SQLite. |

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

---

## 4. Kết quả Kiểm toán Mức độ Sẵn sàng Dữ liệu (`AC1`–`AC4`)

Kiểm toán được thực hiện bằng script `scripts/us_03_check.py` trên bộ dữ liệu chuẩn hóa do `scripts/generate_songnhat_data.py` sinh ra (tổng `1.260` dòng, gồm `1.250` hồ sơ hợp lệ và `10` dòng lỗi kiểm thử Quarantine):

| Tiêu chí kiểm toán | Ngưỡng bắt buộc theo RFP | Kết quả ghi nhận từ `scripts/us_03_check.py` | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`AC1` — Khối lượng tập huấn luyện (`TRAINING_CANDIDATE`)** | `>= 1.000` hồ sơ trưởng thành (`>= 100 Positive`, `>= 100 Negative`); đủ 3 bằng chứng Rubric cho `Positive` & đủ `60 ngày` quan sát cho `Negative`. | **1.000 hồ sơ trưởng thành** (`150 Positive`, `850 Negative`, gồm `20` cặp trùng `account_key` đa kênh); đạt `100%` bằng chứng Rubric & `observer_coverage = Complete`. | **Đạt (`PASS`)** |
| **`AC2` — Tập kiểm thử độc lập nối tiếp (`SEALED_LATER_TEST`)** | `>= 200` hồ sơ trưởng thành (`>= 40 Positive`, `>= 40 Negative`) và tách biệt hoàn toàn theo thời gian sau tập train (`train_t0_max < test_t0_min`). | **200 hồ sơ trưởng thành** (`50 Positive`, `150 Negative`), mốc `t0_time` nằm sau toàn bộ tập train (`test` từ `09/2025`, `train` trong `150 ngày` đầu từ `01/2025`). | **Đạt (`PASS`)** |
| **`AC3` — Chống rò rỉ đặc trưng theo `t0_time`** | `0` bản ghi có `feature_event_time > t0_time` hoặc `feature_available_at > t0_time`; loại bỏ toàn bộ cột `*_DO_NOT_USE`. | **0 dòng rò rỉ** sau `t0_time` (`event_invalid = 0`, `available_invalid = 0`); phát hiện và cắt bỏ đủ **3 cột `*_DO_NOT_USE`** khi nạp SQLite. | **Đạt (`PASS`)** |
| **`AC4` — Độ bao phủ nhãn & Tách dòng lỗi** | Độ bao phủ nhãn trưởng thành (`POSITIVE` + `NEGATIVE` trên tổng `MATURE`) đạt `>= 90%`; hồ sơ chưa đủ 60 ngày giữ `Unknown`. | Độ bao phủ nhãn trên tập `MATURE` đạt **100%** (`>= 90%`); **50 hồ sơ mới (`OPEN_24H`)** giữ nguyên trạng thái `Unknown` (`IMMATURE`); tách an toàn **10 dòng lỗi** (`ERR-0001`–`ERR-0010`) vào Quarantine. | **Đạt (`PASS`)** |
