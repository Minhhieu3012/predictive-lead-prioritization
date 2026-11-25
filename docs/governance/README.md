# Quản trị Cổng Phát hành & Tuân thủ Pháp lý Việt Nam (`docs/governance/`)

Thư mục này lưu trữ các biên bản chốt cổng kiểm soát theo RFP Mục 9 & Mục 12 (`DEL 03`) và hồ sơ tuân thủ pháp lý trước khi vận hành dữ liệu thật (`SEC 03`, `Annex F`).

## Danh mục tài liệu dự kiến (Sprint 4–5)
1. **Biên bản chốt Gate G1 (`gate_g1_signoff.md`):** Đối chiếu báo cáo [`artifacts/gate_g1/gate_g1_benchmark_report.json`](../../artifacts/gate_g1/gate_g1_benchmark_report.json) và [`artifacts/gate_g1/control_fixtures_report.json`](../../artifacts/gate_g1/control_fixtures_report.json) (khóa `FALLBACK_RULES_BASELINE` nếu chưa đạt đủ `G1-C1..G1-C7` trên dữ liệu `REAL`).
2. **Biên bản chốt Gate G2 (`gate_g2_supervised_use_approval.md`):** Phê duyệt chạy Shadow Scoring (`src/models/shadow_runner.py`), kiểm thử RBAC 5 vai trò, đào tạo người dùng và kích hoạt sử dụng có giám sát 20 ngày làm việc (`PLPT-57`).
3. **Biên bản nghiệm thu Gate G3 (`gate_g3_pilot_evidence_report.md`):** Tổng hợp bằng chứng vận hành, công sức tiết kiệm (`>= 20%`), tỷ lệ đúng hạn SLA và theo dõi độ chín nhãn 60 ngày (tách biệt nghiệm thu kỹ thuật với quyết định mở rộng đầu tư).
4. **Hồ sơ Tuân thủ Pháp lý Việt Nam (`vn_legal_compliance_dossier.md`):** Hồ sơ ký duyệt chính thức bởi Privacy & Security Owners phía SongNhat dựa trên [`docs/discovery/us_04_bao_mat_va_phap_ly_vietnam.md`](../discovery/us_04_bao_mat_va_phap_ly_vietnam.md) (Luật 91/2025/QH15, NĐ 356/2025/ND-CP, Luật 134/2025/QH15, NĐ 142/2026/ND-CP, QĐ 33/2026/QĐ-TTg, Luật 116/2025/QH15).
