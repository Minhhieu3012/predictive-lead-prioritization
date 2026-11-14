# US-04 (`PLPT-12`): Rà soát Pháp lý Việt Nam & Bảo mật Dữ liệu trước triển khai

**Dự án:** SongNhat AI-02 — Predictive Lead Prioritization  
---

## 1. Tuân thủ Luật Bảo vệ Dữ liệu Cá nhân Việt Nam (Luật 91/2025/QH15 & NĐ 356/2025/ND-CP)
* **Mục đích xử lý hợp pháp:** Hệ thống chỉ phục vụ sắp xếp thứ tự ưu tiên nội bộ cho đội ngũ Sales B2B của SongNhat; **không** tự động gửi tin nhắn, **không** tự động gọi điện, **không** tự động từ chối khách hàng và **không** chia sẻ dữ liệu ra bên ngoài (hoặc dùng để huấn luyện AI công cộng).
* **Tường lửa tối thiểu hóa dữ liệu (SEC 02 — Cấm đưa vào tính điểm):**
  * **Tuyệt đối loại bỏ:** Họ tên người liên hệ, số điện thoại cá nhân, email cá nhân, thông tin nhân khẩu học nhạy cảm, và toàn bộ ghi chú văn bản tự do (free-text).
  * **Chỉ sử dụng dữ liệu doanh nghiệp có cấu trúc:** Mã định danh ẩn danh (`enquiry_id`, `account_key`), nhóm sản phẩm công nghiệp, mức độ đủ thông số kỹ thuật, ngày dự kiến mua, hạn hợp đồng.

## 2. Phân loại Hệ thống AI theo Luật Trí tuệ nhân tạo Việt Nam (Luật 134/2025/QH15 & NĐ 142/2026/ND-CP) & Luật An ninh mạng (116/2025/QH15)
* **Phân loại sử dụng:** Hệ thống hỗ trợ ra quyết định nội bộ có con người kiểm soát 100% (**Human-in-the-loop**). Điểm số chỉ là thứ hạng tham khảo; nhân viên Sales giữ toàn quyền kiểm tra lý do, ghi đè thứ tự (kèm lý do) và quyết định liên hệ/báo giá/tín dụng.
* **Giới hạn thuật toán:** Tuyệt đối **không dùng mô hình ngôn ngữ lớn (LLM / GenAI)** hay thu thập dữ liệu web công khai để chấm điểm khách hàng. Chỉ dùng bộ quy tắc minh bạch (Rules Baseline) và mô hình bảng đơn giản (Logistic Regression / Cây quyết định nông) nếu được phê duyệt tại Gate GO.
* **Kiểm soát thay đổi & Quyền hạn:** Mọi thao tác chặn khẩn cấp (`POST /api/block`) và xóa/chỉnh sửa dữ liệu phải được ghi nhật ký kiểm toán (Audit Log) và đồng bộ lại khi khôi phục sao lưu (Backup 35 ngày, RTO 4 giờ, RPO 24 giờ).

## 3. Ma trận phân quyền 5 vai trò (RBAC — SEC 01)

| Vai trò | Quyền hạn được cấp | Giới hạn bắt buộc |
| :--- | :--- | :--- |
| **Sales Reader** | Xem hàng đợi theo công suất `K`, xem 3 lý do, ghi nhận liên hệ & ghi đè thứ tự. | Không được xem dữ liệu thô huấn luyện, không được tự ý xóa/ẩn hồ sơ. |
| **Supervisor** | Xem cảnh báo quá hạn 2 ngày, bấm **Chặn khẩn cấp**, xem thống kê ghi đè. | Không được sửa ngược thời gian `t0_time`. |
| **Data Steward** | Chạy nạp 2 file CSV hàng ngày, xử lý dòng lỗi trong `Quarantine`. | Không được chỉnh sửa đặc trưng sau `t0_time`. |
| **Model Reviewer** | Kiểm định rò rỉ dữ liệu, duyệt mô hình hoặc ép quay về Rules Baseline. | Độc lập với người huấn luyện mô hình và đội Sales. |
| **Administrator** | Quản lý tài khoản, khóa bí mật (secrets), sao lưu/khôi phục & cảnh báo sự cố. | Không tự ý thay đổi công thức nghiệp vụ nếu chưa có duyệt của Owner. |

## 4. Quy trình ứng phó sự cố 4 giờ (SEC 08)
Khi phát hiện nghi ngờ rò rỉ dữ liệu cá nhân, truy cập trái phép hoặc sai lệch kiểm soát điểm/chặn khẩn cấp:
1. **Giờ 0 – 1 (Khoanh vùng):** Ngắt ngay cổng API/xuất dữ liệu bị ảnh hưởng (`HOLD` hàng đợi) để chặn phát tán.
2. **Giờ 1 – 2 (Lưu dấu vết):** Lưu giữ toàn bộ nhật ký hệ thống (`audit_log`), bản ghi bị ảnh hưởng và chặn khẩn cấp tài khoản liên quan.
3. **Giờ 2 – 4 (Báo cáo bắt buộc):** Gửi thông báo chính thức cho **Người phụ trách phía SongNhat (Purchaser)** **trong vòng tối đa 4 giờ** kể từ lúc phát hiện.
4. **Sau 4 giờ (Khắc phục):** Vá lỗi, kiểm thử lại toàn bộ chốt an toàn trước khi mở lại hệ thống.
