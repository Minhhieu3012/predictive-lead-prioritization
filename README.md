# SongNhat AI-02 · Predictive Lead Prioritization (PLPT)

**Trạng thái dự án** · Discovery. Nhóm đang khảo sát dữ liệu và thống nhất phạm vi thí điểm. Hệ thống chưa được triển khai.

## Jira Workspace

**Project key** · `PLPT`

- [Jira Workspace – PLPT Board](https://mjnkhieu3012.atlassian.net/jira/software/projects/PLPT/boards/34)
- [Product Backlog](https://mjnkhieu3012.atlassian.net/jira/software/projects/PLPT/boards/34/backlog)

## Product Vision

Hỗ trợ nhân sự kinh doanh Sông Nhật quyết định yêu cầu báo giá hoặc mua hàng đủ điều kiện nào cần xem xét trước trong một phân khúc thiết bị công nghiệp. Nhân sự có thẩm quyền quyết định việc liên hệ, xác nhận cơ hội đủ điều kiện và xử lý thương mại.

## Rules-First Strategy

Nhóm ưu tiên xây dựng hàng đợi theo quy tắc nghiệp vụ dựa trên độ phù hợp sản phẩm, thông số còn thiếu, thời gian chờ và hạn phản hồi. Hàng đợi này là baseline để đánh giá nhánh dự đoán.

Nhánh học máy chỉ được phát triển khi dữ liệu đạt yêu cầu và phạm vi được phê duyệt tại **G0**. Mô hình chỉ được dùng trong thí điểm sau khi chứng minh được khả năng xếp hạng tốt hơn baseline trong đánh giá độc lập và vượt qua các cổng nghiệm thu liên quan. Khi nhánh ML chưa đạt yêu cầu, phương án dự phòng là hàng đợi quy tắc đã được duyệt.

## Phạm vi thí điểm

| Hạng mục | Yêu cầu dự kiến |
| --- | --- |
| Người dùng | 20 người dùng định danh, tối đa 5 phiên đồng thời. Năm vai trò gồm Sales Reader, Supervisor, Data Steward, Model Reviewer và Administrator. |
| Dữ liệu | Tối đa 10.000 yêu cầu lịch sử trong 12–24 tháng, 5.000 yêu cầu đang hoạt động và 30 cột đặc trưng có cấu trúc được duyệt. Khả năng sử dụng dữ liệu lịch sử được xác minh trong Discovery. |
| Nhập liệu | Hai schema CSV UTF-8 gồm ảnh chụp yêu cầu kèm trạng thái xử lý hiện tại và các sự kiện kết quả. Tối đa 1.000 dòng thay đổi mỗi schema mỗi ngày. Đối soát số dòng, hợp nhất yêu cầu trùng, cách ly dòng lỗi và bảo đảm nhập lại không tạo bản ghi trùng. |
| Giao diện | Hiển thị thứ hạng, thứ tự xử lý cuối cùng, chế độ xếp hạng, người phụ trách, hạn phản hồi, tuổi ảnh chụp và tối đa 3 yếu tố giải thích. Hồ sơ chưa chấm điểm hoặc bị cách ly có đường xử lý theo phân quyền. |
| Hiệu năng | Mục tiêu làm mới hàng đợi tối đa 5.000 yêu cầu trong 15 phút. Mục tiêu p95 của giao diện là ≤ 3 giây trên 100 yêu cầu truy cập hợp lệ với 5 phiên đồng thời. |

Ngoài phạm vi cơ bản gồm tự động liên hệ khách hàng, tự động xác nhận hoặc loại bỏ yêu cầu, ghi ngược CRM nguồn, tích hợp CRM hai chiều trực tiếp, GenAI/LLM, thu thập dữ liệu từ web công khai và hiển thị xác suất đã hiệu chỉnh. Điểm ưu tiên dùng để xếp hạng. Dự báo đơn hàng, doanh thu và tác động nhân quả của việc liên hệ nằm ngoài mục tiêu pilot.

## Dữ liệu tại t0 và định nghĩa nhãn

`t0` là thời điểm một yêu cầu riêng biệt lần đầu đủ điều kiện xem xét theo tiêu chí khách quan, trước khi nhân sự sàng lọc. Đặc trưng dự đoán chỉ dùng giá trị đã có thể biết tại hoặc trước `t0`. Chỉnh sửa CRM sau mốc này, báo giá, ghi chú bán hàng và kết quả đơn hàng bị loại khỏi đầu vào dự đoán. Tác vụ hằng ngày cập nhật điều kiện, hạn phản hồi và vị trí hàng đợi trên cơ sở tách biệt với ảnh chụp dùng để chấm điểm.

Tại G0, nhóm và bên mua xác nhận cửa sổ đề xuất **60 ngày dương lịch từ `t0`** và tiêu chí qualification gồm nhu cầu kinh doanh, mức phù hợp sản phẩm và bước tiếp theo đã thống nhất.

| Nhãn | Điều kiện |
| --- | --- |
| Positive | Có bằng chứng xác nhận đủ điều kiện theo rubric trong cửa sổ 60 ngày. Một lần đổi trạng thái CRM chưa đủ làm bằng chứng. |
| Negative | Đã hết 60 ngày, dữ liệu theo dõi đủ để kết luận và không có sự kiện qualification. |
| Unknown / Immature | Chưa hết cửa sổ hoặc dữ liệu theo dõi chưa đủ để xác định kết quả. |

## Kiểm soát hàng đợi và dữ liệu

- Thống nhất năng lực xem xét hằng ngày `K`. Đề xuất dành 10% **suất có thể phân bổ** để chọn ngẫu nhiên trong nhóm đủ điều kiện có thứ hạng thấp hơn. Yêu cầu thay thế khẩn cấp và cam kết phản hồi theo hợp đồng được bảo vệ riêng.
- Theo chính sách được duyệt, chuyển Supervisor xem xét hồ sơ chưa được review sau **2 ngày làm việc**. Ghi nhận quá tải, thao tác điều chỉnh thứ tự và lý do, lần liên hệ và kết quả review.
- Áp dụng chặn khẩn cấp cục bộ tại bước hiển thị và xuất dữ liệu. Tạm giữ hàng đợi bị ảnh hưởng nếu nguồn quá 24 giờ hoặc chưa xác minh được độ mới, theo ngưỡng xác nhận tại G0. Nhân sự kiểm tra quyền liên hệ tại nguồn có thẩm quyền.
- Áp dụng phân quyền, loại thông tin định danh liên hệ và ghi chú tự do khỏi dữ liệu huấn luyện và chấm điểm, hoàn tất các phê duyệt trước khi nạp dữ liệu thật. Theo **SEC 08**, mục tiêu hợp đồng là thông báo cho bên mua trong **4 giờ liên tục** kể từ khi biết về sự cố nghiêm trọng bị nghi ngờ liên quan đến dữ liệu hoặc kiểm soát chấm điểm.

## Lộ trình và cổng nghiệm thu

Kế hoạch gồm 2 tuần Discovery và 12 tuần triển khai, vận hành có giám sát nếu được G0 phê duyệt. Nhóm tổ chức 7 sprint, mỗi sprint 2 tuần.

| Mốc kế hoạch | Điều kiện chính |
| --- | --- |
| **G0 · Cuối tuần 2** | Kiểm tra nút thắt năng lực, quyền sử dụng dữ liệu, lịch sử đặc trưng tại `t0`, bằng chứng qualification và độ bao phủ nhãn. Chốt baseline, `K` và phạm vi. Ngưỡng đề xuất cho ML là ≥ 1.000 yêu cầu riêng biệt đã đủ thời gian quan sát, với ≥ 100 positive và ≥ 100 negative đã xác minh. Dành riêng tập kiểm thử liên tiếp ở giai đoạn muộn hơn, gồm ≥ 200 hồ sơ đã đủ thời gian quan sát với ≥ 40 positive và ≥ 40 negative, giữ tỷ lệ nhãn tự nhiên. Nếu dữ liệu chưa đạt, bên mua có thể duyệt tuyến rules kèm làm sạch và thu thập ảnh chụp mới, hoặc dừng. |
| **G1 · Tuần 6** | Kiểm tra nhập liệu, nhãn, hàng đợi quy tắc và đạt 40/40 Control Fixtures. Với nhánh ML, so sánh hồi quy logistic có regularization với tối đa một mô hình cây có lý do lựa chọn. Trên tập kiểm thử niêm phong, Precision@K phải đạt ≥ 1,20 lần baseline phi dự đoán mạnh hơn đã được duyệt và cận dưới khoảng tin cậy ghép cặp 95% của **mức cải thiện** phải > 0. Tỷ lệ hồ sơ có kết quả đã biết và đủ thời gian quan sát phải đạt ≥ 90% trên tập đủ điều kiện và trên tập được chọn của từng phương pháp. |
| **G2 · Tuần 7** | Rà soát triển khai, kiểm tra phân quyền và phương án dự phòng, đào tạo người dùng. Với ML, chạy chấm điểm ẩn trước và phê duyệt riêng việc hiển thị cho nhân sự. |
| **Vận hành có giám sát · Tuần 8–11** | Thu thập bằng chứng trong ít nhất 20 ngày làm việc. Mục tiêu giảm ≥ 20% công sức chuẩn bị hàng đợi và sàng lọc, đồng thời duy trì kết quả đáp ứng hạn phản hồi ít nhất bằng mức đối chiếu. |
| **G3 · Tuần 14** | Nghiệm thu kiểm soát, quy trình và báo cáo bằng chứng pilot. Ghi nhận các kết quả còn chờ đủ thời gian theo dõi. |
| **G4 · Tuần 14** | Xác minh khôi phục và bàn giao. Mục tiêu RTO 4 giờ trong khung hỗ trợ và RPO 24 giờ. Chỉ định người phụ trách theo dõi sau bàn giao. |
| **Theo dõi kết quả** | Tiếp tục đến hết 60 ngày dương lịch kể từ yêu cầu cuối cùng tham gia pilot, cộng thời gian đối soát đã thống nhất. Quyết định mở rộng dựa trên bằng chứng đủ thời gian quan sát và chi phí thực tế. |

## Quản lý backlog và tiêu chuẩn hoàn thành

| Epic | Phạm vi |
| --- | --- |
| PLPT-1 | Discovery và quyết định phạm vi tại G0 |
| PLPT-2 | Nhập liệu, đối soát và ảnh chụp tại thời điểm quyết định |
| PLPT-3 | Hàng đợi Rules Baseline |
| PLPT-4 | Giao diện và quy trình review |
| PLPT-5 | Mô hình học máy có giám sát, triển khai có điều kiện |
| PLPT-6 | Đánh giá độc lập và đo lường vận hành |
| PLPT-7 | Quản trị, bảo mật và yêu cầu pháp lý |
| PLPT-8 | Vận hành, bàn giao và chuyển giao |

- **Definition of Ready.** Trước khi đưa story vào sprint, kiểm tra INVEST, Epic và mã yêu cầu RFP, tác nhân, tiêu chí nghiệm thu, phụ thuộc và các ràng buộc dữ liệu liên quan.
- **Definition of Done.** Thay đổi kỹ thuật cần kiểm thử đạt yêu cầu, độ phủ nhánh tối thiểu 80% trên mã thay đổi, ít nhất một lượt peer review và bằng chứng đáp ứng tiêu chí nghiệm thu. Bộ 40 Control Fixtures được dùng tại G1.

## Tài liệu tham chiếu

- RFP của Sông Nhật, Phần 2–5, 7–10 và Phụ lục D–E.
- Tài liệu nhóm gồm Product Vision & Product Goal, Project Scope, Product Feature List, Product Roadmap, Stakeholder & Target User Analysis, Definition of Ready và Definition of Done.
