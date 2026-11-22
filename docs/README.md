# Hệ thống Tài liệu Dự án SongNhat AI-02 (`docs/`)

Thư mục `docs/` được phân tầng thành **4 nhóm rõ ranh giới** theo vòng đời dự án và các Gói bàn giao RFP (`DEL 01` – `DEL 04`):

| Thư mục | Phạm vi & Gói bàn giao RFP | Nội dung chính |
| :--- | :--- | :--- |
| [`docs/discovery/`](./discovery/) | **DEL 01 — Discovery (Sprint 1)** | 5 tài liệu nền tảng `us_01` đến `us_05`: mốc `t0_time`, khung 60 ngày, công suất `K`, Rubric 3 điều kiện, cấu trúc 2 file CSV, rà soát pháp lý VN và hồ sơ quyết định Gate G0. |
| [`docs/specs/`](./specs/) | **Đặc tả & Quyết định Kỹ thuật (Sprint 3–4)** | Quyết định nghiệp vụ & kiến trúc chi tiết: `PO-Decisions-Daily-Lead-Workspace.md`, `ML-Pre-T0-Feature-Store-Decisions.md` và các tài liệu Spec của Sprint 4 (`PLPT-56`, `PLPT-57`, `PLPT-58`). |
| [`docs/governance/`](./governance/) | **Quản trị Cổng & Pháp lý (`DEL 03` / SEC 03)** | Biên bản chốt cổng **Gate G1**, **Gate G2**, **Gate G3**, đối chiếu báo cáo đánh giá độc lập (`artifacts/gate_g1/`) và hồ sơ phê duyệt tuân thủ pháp lý Việt Nam. |
| [`docs/handover/`](./handover/) | **Bàn giao & Vận hành (`DEL 04` — Sprint 5)** | Hướng dẫn sử dụng theo **5 vai trò RBAC**, sổ tay vận hành (sao lưu/khôi phục 35 ngày, RTO 4h, RPO 24h, ứng phó sự cố 4 giờ) và quy trình xuất dữ liệu rời bỏ hệ thống (*Portable Exit*). |
