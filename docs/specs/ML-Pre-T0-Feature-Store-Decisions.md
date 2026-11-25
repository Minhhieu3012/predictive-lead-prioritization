# ML Pre-t0 Feature Store — Implementation Decisions (Đối chiếu v16)

**Scope:** `src/features/pre_t0_store.py` (PLPT-26)  
**State:** Đồng bộ hóa với v16 (`APPROVED_FEATURE_DICTIONARY`, `LeakageError`, `guard_no_outcome_join`, `pre_t0_feature_lineage`, masking `account_key`). Các quy tắc US-01 và US-04 vẫn giữ hiệu lực cao nhất.

## 1. Các quy tắc gốc được bảo toàn (v16)

- **Chống rò rỉ hậu $t_0$ (`LeakageError`)**: Mọi đặc trưng phải có `feature_event_time <= t0_time` và `feature_available_at <= t0_time`. Phát hiện cột `*_DO_NOT_USE`, `quotation_value_post_t0`, `order_outcome_post_t0`, hoặc timestamp hậu $t_0$ sẽ ném `LeakageError` ở chế độ `strict_leakage=True`.
- **Cấm join nhãn kết quả (`guard_no_outcome_join`)**: Chặn `label`, `event_type`, `qualification_evidence`, `maturity_state`, và mọi cột `*_DO_NOT_USE` đi vào tập biến dự báo.
- **Từ điển đặc trưng khóa cứng (`APPROVED_FEATURE_DICTIONARY`)**: Giới hạn tối đa `MAX_FEATURE_COLUMNS = 30` (hiện tại 13 cột được phê duyệt), có hàm `get_feature_dictionary_hash()` phục vụ kiểm toán model manifest.
- **Chặn PII & cột định danh (`PROHIBITED_FEATURE_COLUMNS`)**: Loại `enquiry_id`, `account_key`, `contact_name`, `phone`, `email`, `free_text`, `notes`, v.v. khỏi danh sách `feature_cols`.

## 2. Nâng cấp tích hợp từ nhánh 7 thành viên

1. **Ẩn / Không lưu `account_key` (`Account Key Masking`)**:
   - `account_key` chỉ được đọc và sử dụng **trong RAM** để tổng hợp lịch sử pre-$t_0$ (`pre_t0_account_enquiry_count`, `account_history_status`).
   - Không lưu `account_key` vào DataFrame đầu ra của `build_pre_t0_features`, không lưu vào bảng SQLite `pre_t0_features` hay `pre_t0_feature_lineage`, và không in ra log.
2. **Bảng `pre_t0_feature_lineage` riêng biệt**:
   - Hàm `build_pre_t0_features_with_lineage(...)` và `build_pre_t0_store(...)` ghi nhận trạng thái từng bản ghi vào bảng `pre_t0_feature_lineage` (`enquiry_id`, `t0_time`, `feature_event_time`, `feature_available_at`, `feature_status`, `rejection_code`, `feature_version`, `built_at`).
   - Mọi bản ghi vi phạm ranh giới $t_0$ đều được ghi rõ mã lý do từ chối (`FEATURE_EVENT_AFTER_T0`, `FEATURE_AVAILABLE_AFTER_T0`, hoặc `INVALID_T0`) với `feature_status = "REJECTED"` để phục vụ truy vết kiểm toán.
3. **Trạng thái lịch sử tài khoản minh bạch (`account_history_status`)**:
   - Khi có `account_key` và có hồ sơ trước $t_0$: `HISTORY_FOUND`.
   - Khi có `account_key` nhưng không quan sát thấy enquiry trước $t_0$: `NO_PRIOR_ENQUIRY_OBSERVED` (không âm thầm suy diễn thành tài khoản mới hay 0 giao dịch).
   - Khi thiếu `account_key`: `UNKNOWN`.
4. **An toàn đóng kết nối SQLite trên Windows**:
   - Mọi thao tác đọc `clean_enquiries` và ghi `pre_t0_features` / `pre_t0_feature_lineage` trong `build_pre_t0_store` đều bọc bằng `with contextlib.closing(sqlite3.connect(...)) as conn:` kèm `with conn:` để tránh lỗi khóa file `WinError 32`.
