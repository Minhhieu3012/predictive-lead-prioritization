# Tài liệu Bàn giao, Hướng dẫn Sử dụng & Vận hành (`docs/handover/` — DEL 04, Sprint 5)

Thư mục này lưu trữ bộ tài liệu bàn giao vận hành và thoát hệ thống theo RFP Mục 12 (`DEL 04`) và Annex D (`OPS 07`, `OPS 08`).

## Danh mục tài liệu bàn giao (Sprint 5)
1. **Hướng dẫn sử dụng theo 5 vai trò RBAC (`user_guide_5_roles.md`):**
   - `Sales Reader`: đọc hàng đợi theo công suất `K`, xem tối đa 3 lý do phi nhân quả, xác minh quyền liên hệ, ghi nhận review & override.
   - `Supervisor`: rà soát hồ sơ quá hạn 2 ngày làm việc, xem thống kê override và thực hiện chặn khẩn cấp (`POST /api/v1/workspace/block`).
   - `Data Steward`: chạy nạp 2 file CSV hằng ngày (`csv_validator.py`), xử lý hàng đợi `Quarantine` và `steward_review_queue`.
   - `Model Reviewer`: kiểm định rò rỉ dữ liệu, đối chiếu báo cáo Gate G1 và quyết định fallback về `RULES_BASELINE`.
   - `Administrator`: quản trị tài khoản/phiên (tối đa 5 phiên đồng thời), secrets, sao lưu/khôi phục và quy trình sự cố 4 giờ.
2. **Sổ tay vận hành & Khôi phục (`operations_runbook.md`):**
   - Quy trình giám sát hằng ngày (`src/monitoring/drift_monitor.py`), quy trình khóa sự cố `HOLD_STALE_DATA` & báo cáo SongNhat trong 4 giờ (`src/security/incident_hold.py`).
   - Sao lưu SQLite xoay vòng 35 ngày (`RTO 4 giờ`, `RPO 24 giờ`) và khôi phục bắt buộc chạy lại lệnh chặn/xóa (`src/ops/backup_restore.py`).
3. **Quy trình thoát hệ thống trung lập (`portable_exit_guide.md`):**
   - Hướng dẫn chạy `src/ops/portable_exit.py` để xuất dữ liệu được phép, schema DDL và manifest bàn giao sang định dạng trung lập UTF-8 CSV/JSON và kiểm đọc bằng công cụ độc lập (`OPS 08`).

