# BIÊN BẢN KÝ DUYỆT PHÁP LÝ VIỆT NAM TRƯỚC TRIỂN KHAI CÓ GIÁM SÁT (GATE G2)

* **Dự án:** SongNhat AI-02 — Predictive Lead Prioritization
* **Vị trí lưu trữ trong repo:** `artifacts/gate_g2/gate_g2_vietnam_legal_signoff.md`
* **Ticket liên quan:** `PLPT-55` (`US-15` — AC2, AC3), `PLPT-60`, `PLPT-61`
* **Căn cứ:**
  * `SongNhat_Vietnam_Predictive_Lead_Prioritization_RFP.pdf` (Mục 7 `SEC 01`–`SEC 08`, Mục 9 `Gate G2`, Mục 11, Phụ lục C, F `R02`–`R05`, `R10`, `R11`)
  * `docs/discovery/us_04_bao_mat_va_phap_ly_vietnam.md`
  * `artifacts/gate_g2/README.md`
* **Trạng thái hồ sơ:** `CHỜ KÝ DUYỆT` (Cần hoàn tất điều kiện kỹ thuật `PLPT-59`–`PLPT-61` và chữ ký đại diện có thẩm quyền trước khi nạp dữ liệu thật)

---

## 1. Phân định vai trò pháp lý & Phạm vi xử lý (`SEC 03`, `SEC 04`)

| Hạng mục | Nội dung xác nhận |
| :--- | :--- |
| **Bên kiểm soát dữ liệu (`Data Controller`)** | **SongNhat (Purchaser)** — quyết định mục đích, phạm vi, cơ sở pháp lý xử lý dữ liệu và thực hiện nghĩa vụ với chủ thể dữ liệu theo **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15** & **Nghị định 356/2025/NĐ-CP**. |
| **Bên xử lý dữ liệu (`Data Processor`)** | **Team triển khai (Team 08)** và hạ tầng lưu trữ được phê duyệt — chỉ xử lý theo đúng chỉ dẫn bằng văn bản của SongNhat; nghiêm cấm tái sử dụng hoặc huấn luyện mô hình bên ngoài. |
| **Mục đích hợp pháp** | Hỗ trợ sắp xếp thứ tự ưu tiên nội bộ cho đội ngũ Sales B2B (20 người dùng định danh, tối đa 5 phiên đồng thời). **Không** tự động gửi tin nhắn, gọi điện, tạo hợp đồng, sửa kết quả CRM gốc hay từ chối khách hàng (`USR 05`). |
| **Giới hạn phạm vi (`SEC 03`)** | Mở rộng sang tín dụng, tuyển dụng, dịch vụ thiết yếu hoặc mục đích khác bắt buộc lập phạm vi và đánh giá pháp lý lại từ đầu. |

---

## 2. Đánh giá phân loại AI theo pháp luật Việt Nam (`SEC 03`, `R11`)

* **Căn cứ pháp lý:** **Luật Trí tuệ nhân tạo 134/2025/QH15**, **Nghị định 142/2026/NĐ-CP** và **Quyết định 33/2026/QĐ-TTg** (ngày 02/07/2026 về Danh mục hệ thống AI có rủi ro cao).
* **Kết quả phân loại:** Hệ thống hỗ trợ phân loại/ưu tiên yêu cầu báo giá B2B nội bộ với **100% con người kiểm soát (`Human-in-the-loop`)**, không sử dụng LLM/GenAI, chỉ xuất thứ hạng tham khảo và tối đa 3 yếu tố đóng góp phi nhân quả — **không thuộc nhóm hệ thống AI có rủi ro cao**.
* **Lưu ý tuân thủ:** Việc sử dụng nội bộ, có con người rà soát hoặc đặt máy chủ tại Việt Nam tự thân không mặc định miễn trừ nghĩa vụ pháp lý; cần chữ ký phê duyệt chính thức của Chủ sở hữu Pháp lý & Bảo mật SongNhat tại Mục 5.

---

## 3. Lưu trữ, Tối thiểu hóa dữ liệu, Bảo mật & Quyền chủ thể (`SEC 02`, `SEC 04`, `SEC 06`, `SEC 07`)

| Yêu cầu | Quy định áp dụng | Trạng thái đối chiếu hiện tại |
| :--- | :--- | :--- |
| **Địa điểm xử lý & lưu trữ (`SEC 04`)** | Lưu trữ dữ liệu vận hành, nhật ký và bản sao lưu tại Việt Nam; cấm chuyển dữ liệu xuyên biên giới trái phép. | `[Cần điền địa chỉ máy chủ/cloud VN thực tế]` |
| **Thời hạn lưu giữ (`SEC 04`)** | Bản xuất thô: xóa sau **30 ngày**; nhật ký kiểm toán/hàng đợi: **12 tháng**; bản sao lưu xoay vòng: **35 ngày** (`RTO 4 giờ`, `RPO 24 giờ`). | Đã cấu hình retention 35 ngày trong `src/ops/backup_restore.py`. |
| **Tối thiểu hóa dữ liệu (`SEC 02`)** | Loại bỏ họ tên liên hệ, SĐT cá nhân, email cá nhân, nhân khẩu học nhạy cảm và văn bản tự do khỏi tập đặc trưng mô hình. | Đã kiểm soát trong `src/features/pre_t0_store.py`. |
| **Bảo vệ dữ liệu & Tách môi trường (`SEC 06`)** | Mã hóa in-transit và at-rest; secrets tách khỏi file dữ liệu (`ENV_MODE` tách `development` / `evaluation` / `production`); rà quét `gitleaks`. | **Cần hoàn tất:** Bổ sung mã hóa at-rest và job `gitleaks` trên CI (`PLPT-61`). |
| **Quyền chỉnh sửa / hạn chế / xóa (`SEC 07`)** | Đồng bộ xóa/chặn qua `blocked_accounts` và chạy lại khi khôi phục (`restore_backup_with_restriction_replay`). Không cam kết *machine unlearning* tuyệt đối trên artifact đã train. | Đã có hàm replay trong `src/ops/backup_restore.py` và `src/security/incident_hold.py`. |

---

## 4. Quy trình ứng phó sự cố 4 giờ (`SEC 08`)

* **Cam kết SLA:** Thông báo chính thức cho **SongNhat (Purchaser)** trong vòng tối đa **4 giờ** kể từ khi nhận biết sự cố dữ liệu hoặc sai lệch kiểm soát chấm điểm nghiêm trọng (`src/security/incident_hold.py::activate_emergency_hold`, `record_purchaser_notification`).
* **Điều kiện kỹ thuật còn mở trước khi ký Gate G2:** Cần nối `incident_hold.py` vào API `/admin/incident-hold` (`PLPT-60`), hoàn thiện xác thực phiên HMAC-SHA256 + `revoked_sessions` (`PLPT-59`) và lọc quyền `team_id`/`account_key`.

---

## 5. Xác nhận ký duyệt Gate G2

| Vai trò phê duyệt | Họ tên & Chức danh | Kết quả | Ngày ký | Chữ ký |
| :--- | :--- | :--- | :--- | :--- |
| **Chủ sở hữu Pháp lý & Bảo mật (SongNhat)** | `[Họ tên / Chức danh]` | `[ ] Đồng ý  [ ] Yêu cầu bổ sung` | `[DD/MM/YYYY]` | |
| **Product Owner / Accountable Owner (SongNhat)** | `[Họ tên / Chức danh]` | `[ ] Đồng ý  [ ] Yêu cầu bổ sung` | `[DD/MM/YYYY]` | |
| **Delivery Lead / Administrator (Team triển khai)** | `[Họ tên / Chức danh]` | `[ ] Xác nhận đủ điều kiện kỹ thuật` | `[DD/MM/YYYY]` | |
