# US-05 (`PLPT-13`): Hồ sơ Quyết định Phạm vi G0 & Luận chứng Đầu tư (DEL 01)

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
**Người thực hiện:** Phan Minh Hiếu (Team 08) | **Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
 | **Trạng thái:** Hoàn chỉnh

## 1. Tóm tắt điều hành

Tài liệu này tổng hợp toàn bộ kết quả giai đoạn Discovery (Sprint 1: `US-01` đến `US-04`) để trình các bên liên quan phê duyệt giai đoạn triển khai 12 tuần theo chiến lược **Rules-First** (xây dựng bộ quy tắc minh bạch trước, chỉ mở khóa mô hình dự đoán ML khi đạt đủ ngưỡng dữ liệu và hiệu quả kinh tế).

- **Xác nhận nút thắt:** Năng lực duyệt đơn của đội ngũ 20 nhân viên Sales SongNhat được giả định là **nút thắt vận hành thực sự (Có)** theo kịch bản tham chiếu Annex E RFP — xử lý thủ công 500 yêu cầu/tháng tốn 50 giờ, dễ bỏ sót đơn thay thế khẩn cấp và đơn sắp hết hạn hợp đồng _(cần đối chiếu xác nhận bằng số liệu CRM thật cùng Sales Owner)_.
- **Kiểm định dữ liệu (từ `scripts/audit/us_03_check.py`):** **ĐẠT** ngưỡng Gate G0 trên tập kiểm thử pipeline với **1.000 hồ sơ lịch sử trưởng thành** (`150 POSITIVE`, `850 NEGATIVE`), **200 hồ sơ kiểm thử độc lập** (`50 POSITIVE`, `150 NEGATIVE`) và **50 hồ sơ mới (`Unknown` / `OPEN_24H`)**, không có rò rỉ dữ liệu sau `t0_time` _(lưu ý: toàn bộ 1.260 dòng CSV hiện tại gồm 1.250 hợp lệ và 10 dòng lỗi `ERR-0001`–`ERR-0010` là dữ liệu giả lập sinh từ `scripts/dev/generate_synthetic_data.py` (`SYNTHETIC_ONLY`) để kiểm thử pipeline Rules-First MVP; trước Gate G1 bắt buộc phải kiểm toán dữ liệu lịch sử thật của SongNhat đạt tối thiểu `>= 1.000` hồ sơ distinct chín đủ 60 ngày, có ít nhất `100 POSITIVE` và `100 NEGATIVE` đã xác minh)_.
- **Kinh tế (từ Annex E RFP):** Lộ trình **Rules** hoàn vốn trong **~21,1 tháng**; phần tăng thêm của **Predictive (ML)** chỉ đáng đầu tư khi tạo ra tối thiểu **7,458 triệu VND/tháng** lợi ích đóng góp ròng tăng thêm thực tế (_incremental realized contribution_ sau giá vốn, chiết khấu, hàng trả và chi phí tăng thêm).
- **Khuyến nghị chốt:** **Phương án (a) — Triển khai đầy đủ theo chiến lược Rules-First** (đưa Rules Baseline vào vận hành ngay ở Sprint 2–3, đồng thời chạy thử nghiệm song song nhánh ML và chỉ phát hành ML ở Gate G1 nếu vượt Rules tối thiểu **1.20x**).

## 2. Bối cảnh và Mục tiêu 12 tuần

- **Bối cảnh kinh doanh:** SongNhat phân phối động cơ điện (`Motor_DongCo`), biến tần (`Drive_BienTan`), hộp số truyền động (`Transmission_HopSo`) và dịch vụ bảo trì công nghiệp tại Việt Nam.
- **Quy trình hiện tại (As-is):** Nhân viên Sales tự đọc và phân loại thủ công các yêu cầu từ nhiều kênh (Email, Web Form, Zalo OA, Direct Sales), dễ bị trùng lặp khách hàng, chậm phản hồi các ca thay thế khẩn cấp và tốn nhiều thời gian tổng hợp danh sách mỗi sáng.
- **Mục tiêu 12 tuần (theo `US-01` & `US-02`):** Chuẩn hóa mốc `t0_time`, khung quan sát **60 ngày**, bộ tiêu chí Rubric 3 bằng chứng, giảm tối thiểu **20% thời gian chuẩn bị và phân loại hàng đợi** cho 20 nhân viên Sales (5 phiên đồng thời) mà không làm giảm tỷ lệ đúng hạn hợp đồng.

## 3. Xác nhận Nút thắt: Năng lực duyệt đơn của Sales (Tham chiếu Annex E RFP)

| Chỉ số vận hành                    | Giá trị tham chiếu hiện tại                                                                   | Mục tiêu sau triển khai                         | Nhận định                                          |
| :--------------------------------- | :-------------------------------------------------------------------------------------------- | :---------------------------------------------- | :------------------------------------------------- |
| Lượng yêu cầu mới hàng tháng       | ~500 hồ sơ/tháng (~25 hồ sơ/ngày)                                                             | Gom trùng đa kênh, giữ nguyên đầu vào           | Đủ lớn để gây ùn ứ nếu phân loại thủ công.         |
| Thời gian phân loại mỗi hồ sơ      | 6 phút / hồ sơ                                                                                | **3 phút / hồ sơ** (giảm 50%)                   | Tiết kiệm **25 giờ công/tháng** (~3,75 triệu VND). |
| Xử lý ca khẩn cấp & quá hạn        | Dễ bị lẫn với đơn thường                                                                      | Đẩy thẳng lên `TIER_0` & báo quá hạn sau 2 ngày | Bảo vệ cam kết SLA hợp đồng.                       |
| Dữ liệu lịch sử sẵn sàng (`US-03`) | **1.000 train** (`150 POSITIVE / 850 NEGATIVE`) & **200 test** (`50 POSITIVE / 150 NEGATIVE`) | Độ phủ nhãn đạt 100%, 0 rò rỉ `t0`              | **Đủ chuẩn** cho cả Rules và đánh giá nhánh ML.    |

**Kết luận:** Với kịch bản giả định minh họa theo Annex E RFP (`500 hồ sơ/tháng`, chưa phải số liệu CRM thực tế của SongNhat), nút thắt vận hành đã được lượng hóa rõ ràng; hệ thống xếp hạng Rules-First giúp giải phóng ngay **25 giờ/tháng** cho đội Sales ngay khi lên sóng _(giá trị khối lượng và công suất `K` thực tế sẽ được chốt lại cùng Sales Owner trên dữ liệu CRM thật)_.

## 4. Phân tích Kinh tế & Ngưỡng hoàn vốn (Annex E RFP)

| Hạng mục tài chính                | Lộ trình Rules (Bắt buộc)                                                             | Predictive ML (Phần tăng thêm trên nền Rules)                                                   |
| :-------------------------------- | :------------------------------------------------------------------------------------ | :---------------------------------------------------------------------------------------------- |
| **Chi phí đầu tư ban đầu**        | 30 triệu (nhà cung cấp) + 6 triệu (nội bộ) + 3 triệu (3 tháng chờ) = **39 triệu VND** | Tổng 141 triệu -> Tăng thêm **+102 triệu VND** so với Rules                                     |
| **Chi phí vận hành hàng tháng**   | 1 triệu (hệ thống) + 0,6 triệu (4 giờ kiểm tra) = **1,6 triệu/tháng**                 | Tổng 4,2 triệu -> Tăng thêm **+2,6 triệu/tháng** so với Rules                                   |
| **Lợi ích tiết kiệm thời gian**   | 25 giờ/tháng × 150.000đ = **3,75 triệu/tháng** (ròng **+2,15 triệu/tháng**)           | Không giả định tiết kiệm thêm thời gian so với Rules                                            |
| **Điểm hòa vốn / Ngưỡng yêu cầu** | Hoàn vốn sau **~21,1 tháng**                                                          | Cần tối thiểu **≥ 7,458 triệu VND/tháng** đóng góp tăng thêm thực tế để hoàn vốn trong 24 tháng |

- **Phân tích độ nhạy & Ghi chú giả định:** Các số liệu chi phí và sản lượng trên là **kịch bản số học giả định minh họa theo Annex E RFP** để so sánh phương án đầu tư (chưa phải báo giá chính thức hay ROI cam kết của SongNhat). Nếu chỉ dựa vào tiết kiệm thời gian làm việc, nhánh ML **không bao giờ tự hoàn vốn**. Do đó, việc triển khai **Rules trước** là bước đi an toàn tuyệt đối về mặt ROI; nhánh ML chỉ được phép lên Production khi chứng minh được hiệu quả thực tế vượt trội và có báo giá triển khai chính thức.

## 5. Các phương án quyết định (Formal Gate G0)

| Phương án                                                 | Nội dung                                                | Điều kiện áp dụng                                               | Rủi ro chính                                                               |
| :-------------------------------------------------------- | :------------------------------------------------------ | :-------------------------------------------------------------- | :------------------------------------------------------------------------- |
| **(a) Triển khai đầy đủ (Rules-First + ML có điều kiện)** | Làm Rules ở Sprint 2–3; mở nhánh ML kiểm định ở Gate G1 | Nút thắt xác nhận; dữ liệu `US-03` đạt ≥1.000 train & ≥200 test | Mô hình ML không vượt được Rules 1.20x -> tự động quay về Rules (an toàn). |
| **(b) Chỉ triển khai Rules**                              | Chỉ làm đường ống dữ liệu + Rules Baseline              | Nút thắt xác nhận nhưng dữ liệu lịch sử không đủ                | Bỏ lỡ cơ hội tối ưu thêm nếu dữ liệu thực tế có tín hiệu tốt.              |
| **(c) Dừng dự án**                                        | Dừng sau Discovery                                      | Sales xử lý kịp mọi đơn, không có nút thắt                      | Không áp dụng vì bài toán nút thắt & dữ liệu đã đạt.                       |

## 6. Khuyến nghị chốt Gate G0

- **Phương án đề xuất:** **Chọn Phương án (a) — Triển khai 12 tuần theo chiến lược Rules-First + Nhánh ML có điều kiện.**
- **Lý do:** Toàn bộ 4 điều kiện tiền đề đã đạt (`US-01` chốt `t0`/`K`, `US-02` chốt Rubric 3 điều kiện, `US-03` vượt ngưỡng 1.000/200 hồ sơ kiểm thử với 0 rò rỉ, `US-04` hoàn tất rà soát pháp lý VN).
- **Điều kiện cổng cho ML tại Gate G1 (Tuần 6):** Trên tập kiểm thử nối tiếp độc lập sau thời gian huấn luyện (`>= 200` mẫu trưởng thành thực tế), mô hình Logistic Regression / Cây quyết định phải đồng thời đạt: (1) `Precision@K >= 1.20x` so với **baseline phi dự đoán mạnh hơn được phê duyệt** (Rules Baseline / quy trình hiện tại) với cận dưới khoảng tin cậy ghép cặp 95% của mức cải thiện `> 0`; (2) độ bao phủ nhãn trưởng thành `>= 90%`; và (3) vượt qua toàn bộ **40 bài kiểm thử kiểm soát (`40 control fixtures`)**. Nếu không đạt bất kỳ điều kiện nào, hệ thống **tự động giữ nguyên Rules Baseline** (RFP Mục 8 & Mục 9).
- **Người chịu trách nhiệm đo lường:** Product Owner & Model Reviewer (độc lập).

## 7. Rủi ro, Giả định & Biện pháp giảm thiểu

| Rủi ro / Giả định                                   | Tác động                                    | Biện pháp giảm thiểu (đã thiết kế ở `US-01`–`US-04`)                                                                 |
| :-------------------------------------------------- | :------------------------------------------ | :------------------------------------------------------------------------------------------------------------------- |
| **Rò rỉ dữ liệu tương lai sau `t0`**                | Điểm mô hình ảo, sai lệch thực tế           | Khóa cứng `feature_event_time <= t0_time`; loại bỏ mọi cột `_DO_NOT_USE` và thông tin cá nhân (`US-03`, `US-04`).    |
| **Dữ liệu nguồn trễ > 24h hoặc khách bị khiếu nại** | Sales gọi nhầm khách cũ / khách cấm liên hệ | Tự động khóa bảng (`HOLD_STALE_DATA`) khi dữ liệu > 24h và có nút **Chặn khẩn cấp** ngay trên giao diện (`PLPT-17`). |
| **Lộ lọt dữ liệu cá nhân / Vi phạm Luật VN**        | Rủi ro pháp lý (Luật 91/2025 & 134/2025)    | Tường lửa xóa tên/SĐT/email cá nhân; phân quyền 5 vai trò RBAC và quy trình báo sự cố trong **4 giờ** (`US-04`).     |
| **Sales ghi đè thứ tự nhưng không hợp tác**         | Không đo được hiệu quả thực tế              | Bắt buộc chọn mã lý do khi ghi đè (`US-04`), không tự động lấy thao tác ghi đè làm nhãn huấn luyện.                  |

## 8. Xác nhận phê duyệt Gate G0

| Vai trò                             | Phụ trách                                                                                                                | Trách nhiệm ký duyệt (RFP Mục 11 & 14)                                                                   | Quyết định    | Trạng thái                              |
| :---------------------------------- | :----------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------- | :------------ | :-------------------------------------- |
| **Product Owner (PO)**              | Phan Minh Hiếu                                                                                                           | Chốt backlog, phạm vi sản phẩm & điều phối Sprint 2                                                      | Phương án (a) | Chờ ký duyệt phiên Sprint Review        |
| **Scrum Master (SM)**               | Lại Đức Thành                                                                                                            | Điều phối quy trình, hoàn thiện hồ sơ DEL 01                                                             | Phương án (a) | Đã hoàn thiện hồ sơ DEL 01 (09/11/2026) |
| **Lead Dev**                        | Nguyễn Tuấn Du                                                                                                           | Kiến trúc hệ thống, pipeline & kiểm soát kỹ thuật                                                        | Phương án (a) | Đã sẵn sàng triển khai Sprint 2         |
| **Dev (Team 08)**                   | Nguyễn Tuấn Kiệt, Phạm Nguyễn Gia Khang, Đoàn Trần Nguyên Khang, Trần Quốc Khánh, Nguyễn Tấn Tài, Đặng Nguyễn Mạnh Huỳnh | Phát triển đường ống dữ liệu, Rules Engine, API & kiểm thử                                               | Phương án (a) | Đã sẵn sàng triển khai Sprint 2         |
| **Nhà tài trợ (Purchaser Sponsor)** | Đại diện SongNhat                                                                                                        | Phê duyệt phạm vi, ngân sách, năng lực & quyết định tiếp tục/dừng                                        | Phương án (a) | Trình duyệt Gate G0                     |
| **Sales Owner**                     | Đại diện Quản lý Kinh doanh SongNhat                                                                                     | Chốt phân khúc áp dụng, bằng chứng Rubric, công suất `K` thật & chính sách hàng đợi                      | Phương án (a) | Trình duyệt Gate G0                     |
| **Data Steward**                    | Đại diện Quản trị Dữ liệu                                                                                                | Chịu trách nhiệm trích xuất CSV hợp lệ, ánh xạ định danh, dấu thời gian & đối soát nhãn                  | Phương án (a) | Trình duyệt Gate G0                     |
| **Model Reviewer (độc lập)**        | Chuyên gia Thẩm định Độc lập                                                                                             | Phê duyệt từ điển biến, kiểm định rò rỉ dữ liệu, đánh giá theo thời gian & quyết định phát hành/fallback | Phương án (a) | Trình duyệt Gate G0                     |
| **Privacy & Security Owners**       | Phụ trách Pháp lý & An ninh thông tin SongNhat                                                                           | Phê duyệt mục đích xử lý dữ liệu cá nhân, phân loại AI, nhà cung cấp & lưu giữ trước dữ liệu thật        | Phương án (a) | Trình duyệt Gate G0                     |
