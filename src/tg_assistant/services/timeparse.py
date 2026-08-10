from __future__ import annotations

import re
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "thu hai": 0,
    "thu ba": 1,
    "thu tu": 2,
    "thu nam": 3,
    "thu sau": 4,
    "thu bay": 5,
    "chu nhat": 6,
}


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def parse_vietnamese_datetime(
    text: str, *, now: datetime | None = None, timezone: str = "Asia/Ho_Chi_Minh"
) -> datetime:
    tz = ZoneInfo(timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    value = _plain(text).strip()
    base = now
    if "ngay kia" in value:
        base += timedelta(days=2)
    elif "mai" in value:
        base += timedelta(days=1)
    elif "tuan sau" in value:
        base += timedelta(days=7)
    elif "cuoi thang" in value:
        next_month = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        base = next_month - timedelta(days=1)
    for name, weekday in WEEKDAYS.items():
        if name in value:
            delta = (weekday - now.weekday()) % 7
            if delta == 0 or "tuan sau" in value:
                delta += 7
            base = now + timedelta(days=delta)
            break
    hour, minute = (8, 0) if "sang" in value else (14, 0) if "chieu" in value else (now.hour, 0)
    match = re.search(r"(?:truoc\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:gio|h)?", value)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if "chieu" in value and hour < 12:
            hour += 12
    if hour > 23 or minute > 59:
        raise ValueError("Thời gian không hợp lệ")
    return datetime.combine(base.date(), time(hour, minute), tzinfo=tz)
