# PO Decisions — Daily Lead Workspace (AI-02)

> **Mục đích:** Chốt các business rules tối thiểu để đội dev có thể triển khai User Story hiện tại.
> **Vai trò:** Các quyết định dưới đây được xem là PO decisions tạm thời cho phạm vi AI-02, thay cho việc chờ PO.
> **Nguyên tắc:** Chỉ triển khai đúng User Story và AC1–AC4; không mở rộng thành CRM hoặc thêm tính năng ngoài phạm vi.

## 1. Capacity K

- `K = 25` là tổng số lượt review mục tiêu tối đa mỗi ngày của Sales team, **bao gồm cả mandatory work**.
- Mandatory work được đưa vào queue trước discretionary work.
- Nếu số mandatory work lớn hơn K:
  - Vẫn đưa **toàn bộ mandatory work** vào queue.
  - Không lấy bớt mandatory work để giữ hard cap K.
  - Ghi nhận trạng thái `CAPACITY_EXCEEDED` và số lượng vượt K.
  - Không tạo discretionary slots trong ngày đó.
- Nếu mandatory work nhỏ hơn hoặc bằng K, số discretionary slots:

  `D = max(0, K - M)`

  Trong đó `M` là số mandatory items được đưa vào queue trong ngày.

## 2. Mandatory work

Một enquiry là mandatory nếu thỏa **ít nhất một** điều kiện:

1. `is_urgent_replacement = true`.
2. `contract_due_date` nằm trong vòng 0–7 ngày tính từ `t0_time`, giữ nguyên logic Tier 0 hiện có trong `is_tier0_urgent`.

Mandatory work:
- Luôn được ưu tiên đưa vào queue trước các lead thông thường.
- Không được đưa vào nhóm random exploration.
- Không bị loại khỏi queue do điểm thấp hoặc thứ hạng thấp.

`contract_due_date` là nguồn dữ liệu chuẩn để xác định contractual due date. Trong phạm vi AI-02, không áp dụng thêm quy tắc SLA theo số ngày làm việc khác nếu chưa có yêu cầu mới.

Quy tắc 0–7 ngày tính từ `t0_time` được giữ theo logic hiện tại của `is_tier0_urgent`; nó thay thế cách hiểu “đến hạn trong ngày hoặc đã quá hạn tại thời điểm tạo queue” cho phạm vi AI-02 này. Khi không có `t0_time`, giữ fallback hiện có sang `feature_event_time` hoặc thời điểm đánh giá.

## 3. Exploration allocation — 10%

- Dành 10% discretionary review slots cho randomized exploration.
- Exploration chỉ áp dụng trên các enquiry hợp lệ, đã được scoring, không thuộc mandatory work và không nằm trong quarantine.
- Nhóm exploration được lấy ngẫu nhiên từ các eligible enquiries có thứ hạng thấp hơn nhóm được chọn theo ranking thông thường.
- Các discretionary slots còn lại được chọn theo ordinal rank từ cao xuống thấp.
- Không dùng randomization để thay thế hoặc trì hoãn mandatory work.

### Quy tắc số nguyên

Vì số slot là số nguyên, 10% có thể tạo ra phần thập phân. Dùng **fractional carry** theo ngày:

- Mỗi ngày cộng `10% × D` vào quota exploration tích lũy.
- Số exploration slots của ngày hiện tại là phần nguyên của quota tích lũy chưa được sử dụng.
- Giữ lại phần lẻ cho ngày tiếp theo.
- Tổng exploration slots qua nhiều ngày sẽ đạt 10% tổng discretionary slots, sai số làm tròn chỉ nằm ở từng ngày.

Ví dụ: D = 5 mỗi ngày → quota lần lượt tích lũy 0.5, 1.0, 1.5, 2.0... → số slot exploration phát sinh lần lượt 0, 1, 0, 1...

Nếu `D = 0`, không có exploration slot trong ngày.

### Thứ tự phân bổ giữa các cohort

- Rank hiển thị vẫn được tính riêng trong từng cohort; không tạo một global display rank.
- Để áp dụng K team-wide qua nhiều cohort mà không so sánh score giữa cohort, allocation order dùng thứ tự: `cohort_rank` tăng dần, `cohort_id` tăng dần theo chuỗi, rồi `enquiry_id` tăng dần.
- Thứ tự này chỉ dùng để quyết định slot ranked và xác định lower-ranked exploration pool; không thay thế ordinal rank hiển thị.

### Seed tái lập

- Mỗi daily allocation tạo seed một lần và lưu cùng allocation snapshot.
- Cùng seed, cùng candidate snapshot và cùng policy phải tái lập cùng exploration selection.
- Gọi tạo queue lặp lại trong cùng business date trả lại snapshot và seed đã lưu; không random lại.

## 4. Daily allocation lifecycle

- Một daily queue được tạo theo business date, timezone `Asia/Ho_Chi_Minh`.
- Mỗi business date chỉ có một allocation snapshot đang hiệu lực.
- Gọi tạo queue lặp lại trong cùng ngày phải idempotent: không random lại và không làm thay đổi allocation đã lưu.
- Nếu Supervisor cần tạo lại queue, phải là thao tác regenerate có chủ đích và lưu audit record gồm người thực hiện, thời gian và lý do.
- Lưu lại kết quả allocation để có thể giải thích lead nào được chọn theo ranking, lead nào được chọn bằng exploration và vì sao.

## 5. Rank, scoring mode và contributing factors

- Hiển thị **ordinal rank**, không hiển thị rank như xác suất thắng.
- Rank được tính trong từng cohort, không gộp các cohort khác nhau thành một bảng xếp hạng chung.
- `scoring_mode` chỉ nhận một trong hai giá trị:
  - `RULES_BASELINE`
  - `PREDICTIVE_ML`
- Hiện tại sử dụng `RULES_BASELINE` cho đến khi Predictive ML được triển khai và được phép bật.
- Hiển thị tối đa 3 contributing factors thực tế do scoring engine trả về.
- Mỗi factor phải có giá trị được phép hiển thị và source timestamp.
- Không tự tạo factor hoặc timestamp giả để đủ 3 mục.
- Tuyệt đối không hiển thị score dưới dạng calibrated win probability. Ví dụ: score 80 không có nghĩa là 80% cơ hội thắng.

## 6. Unscored và Quarantine

Tách riêng các nhóm hiển thị:

- **Unscored:** enquiry chưa có kết quả scoring hợp lệ.
- **Quarantined:** record bị Data Steward quarantine, kèm quarantine reason nếu có.

Quy tắc:
- Không đưa unscored hoặc quarantined records vào ranking/exploration thông thường.
- Không tự động xóa hoặc discard record có rank thấp.
- Lead có rank thấp vẫn phải được giữ lại và có thể được xem trong workspace phù hợp.
- `Unknown` hoặc `IMMATURE` của outcome **không đồng nghĩa** với `Unscored`; không dùng outcome maturity để suy ra trạng thái scoring.

## 7. AC4 — Concurrency và latency

Acceptance test phải kiểm tra đúng các điều kiện:

- Hỗ trợ 20 named users.
- Có 5 concurrent active sessions.
- Thực hiện tối thiểu 100 requests được xác thực/authorized.
- p95 response latency không vượt quá 3 giây trên tập request kiểm thử.
- Ghi nhận kết quả test, cấu hình môi trường và cách đo latency.

Không được diễn giải “20 named users” thành chỉ cần tạo 20 tài khoản; cũng không được diễn giải “5 concurrent active sessions” thành giới hạn tổng số tài khoản.

## 8. Quy tắc triển khai cho Developer

1. Backend là nơi enforce K, mandatory priority, exploration quota, eligibility và allocation. Frontend chỉ hiển thị kết quả từ backend.
2. Không hard-code K trong frontend. Đọc K từ backend configuration.
3. Không dùng random selection không lưu dấu vết.
4. Không tự động bỏ record chỉ vì rank thấp.
5. Không dùng score như win probability.
6. Không tự thêm business rules ngoài tài liệu này.
7. Nếu source hiện tại mâu thuẫn với quyết định trong tài liệu này, ưu tiên quyết định PO tại đây cho phạm vi AI-02; ghi lại khác biệt trong implementation notes.
8. Giữ các trạng thái scoring, quarantine, allocation và mandatory work tách biệt; không gộp thành một trạng thái chung.

## 9. Definition of Done

- [ ] Daily queue áp dụng K = 25 và xử lý mandatory overflow như mục 1.
- [ ] Urgent replacement và contractual due work luôn được ưu tiên, không bị randomize.
- [ ] Exploration đạt 10% theo fractional carry trên tổng discretionary slots tích lũy.
- [ ] Exploration chỉ chọn từ eligible lower-ranked enquiries.
- [ ] Rank là ordinal rank theo cohort.
- [ ] Scoring mode hiển thị đúng `RULES_BASELINE` hoặc `PREDICTIVE_ML`.
- [ ] Hiển thị contractual due date và tối đa 3 contributing factors có source timestamp.
- [ ] Không có UI/API nào diễn giải score thành win probability.
- [ ] Unscored và Quarantine có view riêng.
- [ ] Low-ranked records không bị tự động xóa hoặc discard.
- [ ] Allocation được persist, idempotent theo ngày và có audit khi regenerate.
- [ ] AC4 được kiểm thử với đúng ngưỡng đã nêu.

## 10. API GET workspace — implementation notes

`GET /api/v1/workspace/queue` accepts `view=Active|Unscored|Quarantine`.

- `model_rank` is score order within a cohort; `final_work_order` is the order
  in the persisted daily allocation after mandatory/ranked/exploration handling.
- `snapshot_age` is expressed in seconds and is `null` until a daily snapshot
  exists.
- The current source schema has no owner/assignee field, so `owner` is returned
  as `null`; this endpoint does not assign leads to users.
- `Unscored` currently means records absent from the persisted daily scoring
  snapshot. There is no durable per-record scoring-status field yet, so this is
  an implementation fallback, not an authoritative business classification.
- Contributor timestamps use the scorer's record-level `t0_time` or
  `feature_event_time`; the source currently does not store a separate timestamp
  for each contributing factor.
- Quarantine output is restricted to non-PII fields, including
  `quarantine_reason`.

## 11. Sales Workspace frontend notes

- The workspace uses separate `Active Queue`, `Unscored Records`, and
  `Quarantine Records` tabs backed by the matching API view.
- The score explanation states that statistical contribution is not evidence
  of causality. Scores are rendered in `points`, never as win percentages.
- Search folds Vietnamese diacritics (including `đ`) and ignores punctuation
  differences to support unaccented queries.

---
