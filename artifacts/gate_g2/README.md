# Bằng chứng Kiểm toán Cổng Gate G2 (`artifacts/gate_g2/`)

Lưu trữ các bằng chứng bất biến (JSON/báo cáo đóng băng) phục vụ phê duyệt **Gate G2 — Supervised Use** (RFP Mục 9):
- Báo cáo kiểm thử chạy ngầm (*Shadow Scoring*) từ `src/models/shadow_runner.py` và kiểm chứng fallback về `RULES_BASELINE` (`src/models/serving_router.py`).
- Bằng chứng kiểm thử RBAC 5 vai trò, giới hạn 5 phiên đồng thời (`PLPT-55`) và khóa sự cố 4 giờ (`SEC 08`).
- Bằng chứng hoàn tất đào tạo người dùng và phê duyệt tách biệt quyền hiển thị hàng đợi cho nhân viên.
