
"""Vietnamese business-day calendar utilities for PLPT-41.

Business days exclude Saturday, Sunday and configured Vietnamese public holidays.
Variable-date holidays (especially Tet and Hung Kings' Commemoration Day) must
be supplied through VN_PUBLIC_HOLIDAYS as comma-separated ISO dates.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Iterable

# Fixed-date Vietnamese public holidays.
_FIXED_HOLIDAYS = {
    (1, 1),    # New Year's Day
    (4, 30),   # Reunification Day
    (5, 1),    # International Labour Day
    (9, 2),    # National Day
}


def get_vietnam_public_holidays(
    year: int,
    additional_holidays: Iterable[date] | None = None,
) -> set[date]:
    """Return configured Vietnamese public holidays for a given year."""

    holidays = {
        date(year, month, day)
        for month, day in _FIXED_HOLIDAYS
    }

    # Variable-date holidays are configured from the official annual calendar.
    configured_dates = os.getenv("VN_PUBLIC_HOLIDAYS", "")

    for raw_date in configured_dates.split(","):
        raw_date = raw_date.strip()
        if not raw_date:
            continue

        try:
            holiday = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError(
                f"Invalid VN_PUBLIC_HOLIDAYS date: {raw_date}. "
                "Expected YYYY-MM-DD."
            ) from error

        if holiday.year == year:
            holidays.add(holiday)

    if additional_holidays:
        holidays.update(
            holiday
            for holiday in additional_holidays
            if holiday.year == year
        )

    return holidays


def is_business_day(
    value: date,
    holidays: Iterable[date] | None = None,
) -> bool:
    """Return True when the date is a Vietnamese working day."""

    if isinstance(value, datetime):
        value = value.date()

    if value.weekday() >= 5:
        return False

    holiday_set = (
        set(holidays)
        if holidays is not None
        else get_vietnam_public_holidays(value.year)
    )

    return value not in holiday_set


def add_business_days(
    start: date | datetime,
    days: int,
    holidays: Iterable[date] | None = None,
) -> date | datetime:
    """Add business days, preserving datetime time and timezone information.

    The starting date is not counted. For example, adding two business days
    to Tuesday returns Thursday, unless a holiday changes the calculation.
    """

    if days < 0:
        raise ValueError("days must be non-negative")

    result = start
    remaining = days

    while remaining > 0:
        result += timedelta(days=1)

        current_date = (
            result.date()
            if isinstance(result, datetime)
            else result
        )

        year_holidays = (
            set(holidays)
            if holidays is not None
            else get_vietnam_public_holidays(current_date.year)
        )

        if is_business_day(current_date, year_holidays):
            remaining -= 1

    return result


def is_overdue_after_business_days(
    eligible_at: datetime,
    now: datetime,
    business_days: int = 2,
    holidays: Iterable[date] | None = None,
) -> bool:
    """Check whether the configured number of business days has elapsed."""

    if business_days < 0:
        raise ValueError("business_days must be non-negative")

    if eligible_at.tzinfo != now.tzinfo:
        raise ValueError(
            "eligible_at and now must use compatible timezone information"
        )

    deadline = add_business_days(
        eligible_at,
        business_days,
        holidays=holidays,
    )

    return now >= deadline
def is_enquiry_overdue_2bd(
    eligible_at: datetime,
    first_reviewed_at: datetime | None,
    now: datetime,
    business_days: int = 2,
    holidays: Iterable[date] | None = None,
) -> bool:
    """Kiểm tra chính xác cờ is_overdue_2bd cho Enquiry.
    Nếu đã có first_reviewed_at thì chắc chắn không bị quá hạn unreviewed (False).
    Nếu chưa review, kiểm tra xem đã quá số ngày làm việc quy định hay chưa.
    """
    if first_reviewed_at is not None:
        return False
        
    return is_overdue_after_business_days(
        eligible_at=eligible_at,
        now=now,
        business_days=business_days,
        holidays=holidays,
    )