# US-04 (`PLPT-12`): Rà soát Pháp lý Việt Nam & Bảo mật Dữ liệu trước triển khai

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
**Người thực hiện:** Phan Minh Hiếu (Team 08) | **Ngày:** 9/11/2026 | **Trạng thái:** Hoàn chỉnh

---

## 1. Tuân thủ Luật Bảo vệ Dữ liệu Cá nhân Việt Nam (Luật 91/2025/QH15 & NĐ 356/2025/ND-CP)

- **Phân định vai trò pháp lý & Lưu trữ dữ liệu (`AC1`, `SEC 03`, `SEC 04`):**
  - **Bên kiểm soát dữ liệu (`Data Controller`):** **SongNhat (Purchaser)** — quyết định mục đích, phạm vi, cơ sở pháp lý xử lý dữ liệu và thực hiện các quyền/nghĩa vụ đối với chủ thể dữ liệu theo Luật 91/2025/QH15.
  - **Bên xử lý dữ liệu (`Data Processor`):** **Team triển khai (Team 08) & hạ tầng lưu trữ được phê duyệt** — chỉ xử lý dữ liệu theo đúng chỉ dẫn và phạm vi ủy quyền của SongNhat, tuyệt đối không tái sử dụng dữ liệu cho mục đích khác hoặc huấn luyện mô hình ngoài hệ thống.
  - **Điều kiện lưu trữ, lưu giữ & chuyển dữ liệu (`SEC 04`, `SEC 07`, `Annex C`):** Toàn bộ dữ liệu vận hành, nhật ký và bản sao lưu bắt buộc **lưu trữ tại Việt Nam**; **cấm chuyển dữ liệu xuyên biên giới trái phép** hoặc gửi sang bất kỳ bên thứ ba/nhà thầu phụ nào chưa được SongNhat phê duyệt trước bằng văn bản. Áp dụng giới hạn lưu giữ đề xuất: bản xuất thô (raw exports) xóa sau **30 ngày** sau khi đối soát xong, nhật ký hàng đợi/kiểm toán giữ tối thiểu cần thiết **12 tháng**, bản sao lưu xoay vòng **35 ngày**. Khi có yêu cầu xóa dữ liệu chủ thể (`SEC 07`), thực hiện xóa trên hàng đợi, kho lưu trữ, bản xuất và đồng bộ khi khôi phục sao lưu; đồng thời lưu ý **không cam kết xóa tuyệt đối trong artifact mô hình đã huấn luyện** (_no perfect machine unlearning_), mà phải đánh giá giới hạn hoặc thu hồi artifact liên quan.
- **Mục đích xử lý hợp pháp:** Hệ thống chỉ phục vụ sắp xếp thứ tự ưu tiên nội bộ cho đội ngũ Sales B2B của SongNhat; **không** tự động gửi tin nhắn, **không** tự động gọi điện, **không** tự động từ chối khách hàng và **không** chia sẻ dữ liệu ra bên ngoài (hoặc dùng để huấn luyện AI công cộng).
- **Tường lửa tối thiểu hóa dữ liệu (SEC 02 — Cấm đưa vào tính điểm):**
  - **Tuyệt đối loại bỏ:** Họ tên người liên hệ, số điện thoại cá nhân, email cá nhân, thông tin nhân khẩu học nhạy cảm, và toàn bộ ghi chú văn bản tự do (free-text).
  - **Chỉ sử dụng dữ liệu doanh nghiệp có cấu trúc:** Mã định danh ẩn danh (`enquiry_id`, `account_key`), nhóm sản phẩm công nghiệp, mức độ đủ thông số kỹ thuật, ngày dự kiến mua, hạn hợp đồng. _(Lưu ý `SEC 02` & `Annex C`: mã định danh giả danh và thông tin liên hệ doanh nghiệp vẫn có thể cấu thành dữ liệu cá nhân gắn với cá nhân liên hệ, do đó vẫn chịu sự kiểm soát truy cập và căn cứ xử lý hợp pháp)._

## 2. Phân loại Hệ thống AI theo Luật Trí tuệ nhân tạo Việt Nam (Luật 134/2025/QH15 & NĐ 142/2026/ND-CP) & Luật An ninh mạng (116/2025/QH15)

- **Phân loại sử dụng:** Hệ thống hỗ trợ ra quyết định nội bộ có con người kiểm soát 100% (**Human-in-the-loop**). Điểm số chỉ là thứ hạng tham khảo; nhân viên Sales giữ toàn quyền kiểm tra lý do, ghi đè thứ tự (kèm lý do) và quyết định liên hệ/báo giá/tín dụng.
- **Đối chiếu Danh mục AI rủi ro cao (`AC2`, `SEC 03`, `Annex F R11`):** Đã đối chiếu **Quyết định 33/2026/QĐ-TTg** (Danh mục hệ thống AI có rủi ro cao ngày 02/07/2026) cho thấy phạm vi hỗ trợ phân loại/ưu tiên yêu cầu báo giá nội bộ B2B của SongNhat **không thuộc nhóm hệ thống AI có rủi ro cao** (chỉ hỗ trợ sắp xếp thứ tự tham khảo cho nhân viên Sales, không tự động đánh giá tín dụng, tuyển dụng hay dịch vụ thiết yếu). Tuy nhiên, việc sử dụng nội bộ, có con người rà soát hay lưu trữ tại Việt Nam tự thân **không mặc định miễn trừ nghĩa vụ pháp lý**: **Chủ sở hữu Pháp lý & Bảo mật của SongNhat** phải ký duyệt văn bản đánh giá phân loại AI, mục đích xử lý và thông báo minh bạch trước khi nạp dữ liệu thật; mọi mở rộng sang mục đích khác (tín dụng, tuyển dụng, dịch vụ thiết yếu...) đều bắt buộc đánh giá lại từ đầu.
- **Giới hạn thuật toán:** Tuyệt đối **không dùng mô hình ngôn ngữ lớn (LLM / GenAI)** hay thu thập dữ liệu web công khai để chấm điểm khách hàng. Chỉ dùng bộ quy tắc minh bạch (Rules Baseline) và mô hình bảng đơn giản (Logistic Regression / Cây quyết định nông) nếu được phê duyệt tại Gate G0.
- **Kiểm soát An ninh mạng & Khôi phục (`SEC 06`, `OPS 07`, Luật `116/2025/QH15`):** Mã hóa dữ liệu khi truyền tải và lưu trữ; quản lý khóa bí mật (secrets) tách biệt khỏi tệp dữ liệu; rà quét tệp import, cập nhật vá lỗi phụ thuộc, hạn chế kết nối outbound và tách biệt hoàn toàn môi trường đánh giá với môi trường sản xuất. Mọi thao tác chặn khẩn cấp (`POST /api/block`) và xóa/chỉnh sửa dữ liệu phải được ghi nhật ký kiểm toán (`Audit Log`) và đồng bộ/chạy lại trước khi mở quyền truy cập sau khôi phục sao lưu (**Backup 35 ngày, RTO 4 giờ, RPO 24 giờ**, mục tiêu sẵn sàng **99.5%** trong giờ làm việc 08:00–17:00 giờ Việt Nam).

## 3. Ma trận phân quyền 5 vai trò (RBAC — SEC 01)

| Vai trò            | Quyền hạn được cấp                                                              | Giới hạn bắt buộc                                                    |
| :----------------- | :------------------------------------------------------------------------------ | :------------------------------------------------------------------- |
| **Sales Reader**   | Xem hàng đợi theo công suất `K`, xem 3 lý do, ghi nhận liên hệ & ghi đè thứ tự. | Không được xem dữ liệu thô huấn luyện, không được tự ý xóa/ẩn hồ sơ. |
| **Supervisor**     | Xem cảnh báo quá hạn 2 ngày, bấm **Chặn khẩn cấp**, xem thống kê ghi đè.        | Không được sửa ngược thời gian `t0_time`.                            |
| **Data Steward**   | Chạy nạp 2 file CSV hàng ngày, xử lý dòng lỗi trong `Quarantine`.               | Không được chỉnh sửa đặc trưng sau `t0_time`.                        |
| **Model Reviewer** | Kiểm định rò rỉ dữ liệu, duyệt mô hình hoặc ép quay về Rules Baseline.          | Độc lập với người huấn luyện mô hình và đội Sales.                   |
| **Administrator**  | Quản lý tài khoản, khóa bí mật (secrets), sao lưu/khôi phục & cảnh báo sự cố.   | Không tự ý thay đổi công thức nghiệp vụ nếu chưa có duyệt của Owner. |

- **Kiểm tra truy cập bắt buộc (`SEC 01`):** Hệ thống xác thực danh tính và kiểm tra phân quyền trước khi trả về hàng đợi, giải thích điểm số hay xuất dữ liệu; bắt buộc kiểm thử chặn URL truy cập trực tiếp (direct URLs) và phiên đăng nhập hết hạn (stale sessions).

## 4. Quy trình ứng phó sự cố 4 giờ (SEC 08)

Khi phát hiện nghi ngờ rò rỉ dữ liệu cá nhân, truy cập trái phép hoặc sai lệch kiểm soát điểm/chặn khẩn cấp:

1. **Giờ 0 – 1 (Khoanh vùng):** Ngắt ngay cổng API/xuất dữ liệu bị ảnh hưởng (`HOLD_STALE_DATA`) để chặn phát tán.
2. **Giờ 1 – 2 (Lưu dấu vết):** Lưu giữ toàn bộ nhật ký hệ thống (`audit_log`), bản ghi bị ảnh hưởng và chặn khẩn cấp tài khoản liên quan.
3. **Giờ 2 – 4 (Báo cáo bắt buộc):** Gửi thông báo chính thức cho **Người phụ trách phía SongNhat (Purchaser)** **trong vòng tối đa 4 giờ** kể từ lúc phát hiện.
4. **Sau 4 giờ (Khắc phục):** Vá lỗi, kiểm thử lại toàn bộ chốt an toàn trước khi mở lại hệ thống.
