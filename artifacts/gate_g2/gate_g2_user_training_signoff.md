# BIÊN BẢN XÁC NHẬN ĐÀO TẠO NGƯỜI DÙNG TRƯỚC TRIỂN KHAI CÓ GIÁM SÁT (GATE G2)

* **Dự án:** SongNhat AI-02 — Predictive Lead Prioritization
* **Vị trí lưu trữ trong repo:** `artifacts/gate_g2/gate_g2_user_training_signoff.md`
* **Ticket liên quan:** `PLPT-55` (`US-15` — AC3), `PLPT-57` (`US-17`), `PLPT-58` (`US-18`)
* **Căn cứ:**
  * `SongNhat_Vietnam_Predictive_Lead_Prioritization_RFP.pdf` (Mục 5 `USR 01`–`USR 07`, Mục 9 `Gate G2`, Mục 11, trang 13)
  * `Stakeholder & Target User Analysis.md`
  * `artifacts/gate_g2/README.md`
* **Trạng thái hồ sơ:** `CHỜ XÁC NHẬN HOÀN TẤT ĐÀO TẠO`

---

## 1. Mục tiêu bắt buộc để qua cổng Gate G2 (RFP trang 13 & `PLPT-55` AC3)

Trước khi mở sử dụng có giám sát cho **20 người dùng định danh** (tối đa **5 phiên đồng thời**), toàn bộ nhân sự tham gia pilot phải được đào tạo và xác nhận nắm vững 4 nguyên tắc cốt lõi:

1. **Phân biệt Thứ hạng (`Ordinal Rank`), Xác suất (`Probability`) và Điểm quy tắc (`Rules Score`):** Điểm số và thứ hạng chỉ là công cụ tham khảo nội bộ; thứ hạng ưu tiên không đồng nghĩa với xác suất chốt đơn tuyệt đối hay hiệu quả doanh thu nhân quả.
2. **Ranh giới quyền hạn & Nghĩa vụ nghiệp vụ (`USR 04`, `USR 05`):** Điểm số **không** cấp quyền định giá, cấp tín dụng, ký hợp đồng hay quyền tự động liên hệ khách hàng. Nhân viên bắt buộc kiểm tra lý do, thông tin thiếu và quyền liên hệ hợp pháp trước khi hành động.
3. **Xử lý hồ sơ chưa chấm điểm (`Unscored`), Quá hạn 2 ngày (`Overdue 2BD`) & Ghi đè (`Override`):** Không tự ý bỏ qua hồ sơ `unscored` hoặc bị tràn ngoài `Top-K`; `Supervisor` rà soát hồ sơ quá hạn 2 ngày làm việc; thao tác ghi đè thứ tự (`Override`) bắt buộc ghi lý do và chỉ là phản hồi vận hành, không phải nhãn huấn luyện (`USR 06`).
4. **Hạn chế khẩn cấp & Khóa sự cố (`USR 07`, `SEC 08`):** Khi phát hiện yêu cầu ngừng liên hệ hoặc nghi ngờ sự cố dữ liệu, `Supervisor`/`Administrator` thực hiện chặn khẩn cấp tại chỗ và kích hoạt quy trình báo cáo sự cố 4 giờ (`HOLD_STALE_DATA`).

---

## 2. Nội dung đào tạo theo 5 vai trò RBAC (`SEC 01`)

| Vai trò RBAC | Số lượng dự kiến | Nội dung đào tạo bắt buộc | Tiêu chí đạt |
| :--- | :--- | :--- | :--- |
| **Sales Reader** | `[Số lượng]` / 20 | Đọc hàng đợi theo công suất `K`, phân biệt `final_work_order` với `model_rank`, kiểm tra tối đa 3 lý do, xử lý mục `unscored`, ghi nhận `ACCEPT_PRIORITY` / `OVERRIDE_PRIORITY` kèm lý do và đo thời gian phân loại. | Thực hành đúng thao tác trên Workspace, không nhầm rank với quyền liên hệ/báo giá. |
| **Supervisor** | `[Số lượng]` | Theo dõi cảnh báo quá hạn **2 ngày làm việc**, rà soát thống kê ghi đè của đội Sales, thực hiện **Chặn khẩn cấp** (`enquiry_id` / `account_key`) và kiểm tra trạng thái `HOLD_STALE_DATA`. | Thực hành thành công chặn khẩn cấp và kiểm tra hồ sơ biến mất khỏi hàng đợi. |
| **Data Steward** | `[Số lượng]` | Quy trình nạp 2 file CSV hằng ngày (`enquiries_snapshot.csv`, `outcome_events.csv`), xử lý bản ghi lỗi trong `Quarantine` và hàng đợi trùng lặp `steward_review_queue` (không sửa đặc trưng sau `t0_time`). | Nắm vững quy trình tách lỗi không làm hỏng hàng đợi chính. |
| **Model Reviewer** | `[Số lượng]` | Đọc báo cáo Gate G1 (`RULES_BASELINE` vs `PREDICTIVE_ML`), theo dõi dashboard Drift/Coverage (`>= 10 pp` missingness, `< 90%` coverage), quy tắc cấm tự động retrain (`auto_retrain_enabled = false`) và kích hoạt fallback về `RULES_BASELINE`. | Giải thích đúng ý nghĩa 2 ngưỡng điều tra và cơ chế fallback. |
| **Administrator** | `[Số lượng]` | Quản lý danh bạ 20 tài khoản nội bộ, giới hạn 5 phiên đồng thời, thu hồi phiên (`revoked_sessions`), quản lý secrets theo `ENV_MODE`, sao lưu/khôi phục 35 ngày (kèm replay lệnh chặn/xóa) và quy trình sự cố 4 giờ. | Thực hành đúng khóa sự cố 4 giờ và khôi phục kèm replay hạn chế. |

---

## 3. Danh sách xác nhận tham gia đào tạo (Mẫu điền khi tổ chức buổi đào tạo)

| STT | Họ và tên | Mã tài khoản (`user_id`) | Vai trò RBAC | Ngày hoàn tất | Kết quả kiểm tra hiểu biết | Xác nhận của học viên |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `[Họ tên]` | `sales_north_01` | `SALES_READER` — `TEAM-NORTH` (ACCT-N01, ACCT-N02) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 2 | `[Họ tên]` | `sales_north_02` | `SALES_READER` — `TEAM-NORTH` (ACCT-N03, ACCT-N04) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 3 | `[Họ tên]` | `sales_north_03` | `SALES_READER` — `TEAM-NORTH` (ACCT-N05, ACCT-N06) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 4 | `[Họ tên]` | `sales_north_04` | `SALES_READER` — `TEAM-NORTH` (ACCT-N07, ACCT-N08) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 5 | `[Họ tên]` | `sales_central_01` | `SALES_READER` — `TEAM-CENTRAL` (ACCT-C01, ACCT-C02) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 6 | `[Họ tên]` | `sales_central_02` | `SALES_READER` — `TEAM-CENTRAL` (ACCT-C03, ACCT-C04) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 7 | `[Họ tên]` | `sales_central_03` | `SALES_READER` — `TEAM-CENTRAL` (ACCT-C05, ACCT-C06) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 8 | `[Họ tên]` | `sales_south_01` | `SALES_READER` — `TEAM-SOUTH` (ACCT-S01, ACCT-S02) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 9 | `[Họ tên]` | `sales_south_02` | `SALES_READER` — `TEAM-SOUTH` (ACCT-S03, ACCT-S04) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 10 | `[Họ tên]` | `sales_south_03` | `SALES_READER` — `TEAM-SOUTH` (ACCT-S05, ACCT-S06) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 11 | `[Họ tên]` | `sales_south_04` | `SALES_READER` — `TEAM-SOUTH` (ACCT-S07, ACCT-S08) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 12 | `[Họ tên]` | `sales_south_05` | `SALES_READER` — `TEAM-SOUTH` (ACCT-S09, ACCT-S10) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 13 | `[Họ tên]` | `sup_north_01` | `SUPERVISOR` — `TEAM-NORTH` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 14 | `[Họ tên]` | `sup_central_01` | `SUPERVISOR` — `TEAM-CENTRAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 15 | `[Họ tên]` | `sup_south_01` | `SUPERVISOR` — `TEAM-SOUTH` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 16 | `[Họ tên]` | `steward_01` | `DATA_STEWARD` — `GLOBAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 17 | `[Họ tên]` | `steward_02` | `DATA_STEWARD` — `GLOBAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 18 | `[Họ tên]` | `reviewer_01` | `MODEL_REVIEWER` — `GLOBAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 19 | `[Họ tên]` | `reviewer_02` | `MODEL_REVIEWER` — `GLOBAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |
| 20 | `[Họ tên]` | `admin_01` | `ADMINISTRATOR` — `GLOBAL` (toàn đội/toàn cục) | `[DD/MM/YYYY]` | `[ ] Đạt  [ ] Cần ôn lại` | |

---

## 4. Xác nhận hoàn tất đào tạo cho Gate G2

| Vai trò xác nhận | Họ tên & Chức danh | Kết quả | Ngày ký | Chữ ký |
| :--- | :--- | :--- | :--- | :--- |
| **Sales Owner / Supervisor Lead (SongNhat)** | `[Họ tên / Chức danh]` | `[ ] Đủ điều kiện vận hành pilot` | `[DD/MM/YYYY]` | |
| **Delivery Lead / Trainer (Team triển khai)** | `[Họ tên / Chức danh]` | `[ ] Đã hoàn tất hướng dẫn 5 vai trò` | `[DD/MM/YYYY]` | |
| **Product Owner (SongNhat)** | `[Họ tên / Chức danh]` | `[ ] Chấp thuận mở cổng đào tạo G2` | `[DD/MM/YYYY]` | |
