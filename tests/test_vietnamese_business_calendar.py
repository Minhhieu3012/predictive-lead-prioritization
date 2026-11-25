
from datetime import date, datetime, timedelta

import pytest

from src.rules.vietnamese_business_calendar import (
    add_business_days,
    get_vietnam_public_holidays,
    is_business_day,
    is_overdue_after_business_days,
)

VN_TZ = timezone = timedelta(hours=7)


def test_saturday_is_not_business_day():
    assert is_business_day(date(2026, 11, 21)) is False


def test_sunday_is_not_business_day():
    assert is_business_day(date(2026, 11, 22)) is False


def test_normal_business_day():
    assert is_business_day(date(2026, 11, 17)) is True


def test_fixed_vietnamese_holiday():
    assert is_business_day(date(2026, 9, 2)) is False


def test_add_two_business_days():
    start = date(2026, 11, 17)  # Tuesday

    result = add_business_days(start, 2)

    assert result == date(2026, 11, 19)  # Thursday


def test_add_two_business_days_across_weekend():
    start = date(2026, 11, 20)  # Friday

    result = add_business_days(start, 2)

    assert result == date(2026, 11, 24)  # Tuesday


def test_add_business_days_excludes_additional_holiday():
    start = date(2026, 11, 17)  # Tuesday
    holidays = {date(2026, 11, 18)}

    result = add_business_days(start, 2, holidays)

    assert result == date(2026, 11, 20)  # Friday


def test_overdue_after_two_business_days():
    start = datetime(2026, 11, 17, 9, 0)
    now = datetime(2026, 11, 19, 9, 0)

    assert is_overdue_after_business_days(start, now) is True


def test_not_overdue_before_deadline():
    start = datetime(2026, 11, 17, 9, 0)
    now = datetime(2026, 11, 19, 8, 59)

    assert is_overdue_after_business_days(start, now) is False


def test_not_overdue_when_first_review_is_before_deadline():
    start = datetime(2026, 11, 17, 9, 0)
    reviewed_at = datetime(2026, 11, 18, 15, 0)

    assert is_overdue_after_business_days(start, reviewed_at) is False


def test_negative_business_days_rejected():
    with pytest.raises(ValueError):
        add_business_days(date(2026, 11, 17), -1)
