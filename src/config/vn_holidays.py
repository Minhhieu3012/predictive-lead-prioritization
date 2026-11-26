"""PLPT-25 / VN-01: Cấu hình ngày nghỉ lễ Việt Nam (dễ kiểm thử & cập nhật)."""

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path

VN_TZ = timezone(timedelta(hours=7))

# Ngày nghỉ lễ dương lịch cố định hàng năm (tháng, ngày)
VN_FIXED_PUBLIC_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),   # Tết Dương lịch
    (4, 30),  # Ngày Giải phóng miền Nam
    (5, 1),   # Ngày Quốc tế Lao động
    (9, 2),   # Quốc khánh
}

# Ngày nghỉ lễ âm lịch / nghỉ bù theo năm (ISO YYYY-MM-DD)
VN_VARIABLE_PUBLIC_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 4, 26),  # Giỗ Tổ Hùng Vương 2026
    date(2026, 4, 27),  # Nghỉ bù Giỗ Tổ Hùng Vương
    date(2026, 9, 1),   # Nghỉ liền kề Quốc khánh
}


def load_custom_holidays() -> set[date]:
    """Cho phép nạp thêm danh sách ngày lễ từ biến môi trường VN_HOLIDAYS_FILE (JSON list ISO dates)."""
    custom_path = os.getenv("VN_HOLIDAYS_FILE", "").strip()
    if not custom_path:
        return set()
    p = Path(custom_path)
    if not p.exists():
        return set()
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        return {date.fromisoformat(str(x)[:10]) for x in items}
    except Exception:
        return set()


def is_vn_business_day(d: date, extra_holidays: set[date] | None = None) -> bool:
    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in VN_FIXED_PUBLIC_HOLIDAYS:
        return False
    if d in VN_VARIABLE_PUBLIC_HOLIDAYS:
        return False
    if d in load_custom_holidays():
        return False
    if extra_holidays and d in extra_holidays:
        return False
    return True


def count_vn_business_days(
    start_dt: datetime,
    end_dt: datetime,
    extra_holidays: set[date] | None = None,
) -> int:
    """Đếm số ngày làm việc Việt Nam trọn vẹn trôi qua giữa start_dt và end_dt."""
    if end_dt <= start_dt:
        return 0
    start_d = start_dt.astimezone(VN_TZ).date() if start_dt.tzinfo else start_dt.date()
    end_d = end_dt.astimezone(VN_TZ).date() if end_dt.tzinfo else end_dt.date()
    if end_d <= start_d:
        return 0
    days = 0
    cur = start_d + timedelta(days=1)
    while cur <= end_d:
        if is_vn_business_day(cur, extra_holidays=extra_holidays):
            days += 1
        cur += timedelta(days=1)
    return days

