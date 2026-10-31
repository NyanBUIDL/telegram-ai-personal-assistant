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
    **{f"thu {number}": number - 2 for number in range(2, 8)},
}
TIME_GUIDANCE = (
    "Thời gian chưa rõ hoặc không hợp lệ. Ví dụ: 9 giờ tối mai, sáng thứ Hai, "
    "mai lúc 21:30. Chưa hỗ trợ thời gian tương đối như 'sau 2 giờ'."
)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return " ".join(
        "".join(c for c in normalized if unicodedata.category(c) != "Mn").split()
    )


def parse_vietnamese_datetime(
    text: str, *, now: datetime | None = None, timezone: str = "Asia/Ho_Chi_Minh"
) -> datetime:
    tz = ZoneInfo(timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    value = _plain(text)
    if not value or len(value) > 200:
        raise ValueError(TIME_GUIDANCE)
    dayparts = re.findall(r"\b(?:sang|trua|chieu|toi|dem)\b", value)
    weekdays = re.findall(r"\b(?:thu (?:hai|ba|tu|nam|sau|bay|[2-7])|chu nhat)\b", value)
    dates = re.findall(r"\b(?:ngay kia|ngay mai|hom nay|mai|tuan sau|cuoi thang)\b", value)
    if len(dayparts) > 1 or len(weekdays) > 1 or len(dates) > 1:
        raise ValueError(TIME_GUIDANCE)
    if weekdays and dates and dates[0] != "tuan sau":
        raise ValueError(TIME_GUIDANCE)

    base = now
    date_phrase = dates[0] if dates else None
    if weekdays:
        weekday = WEEKDAYS[weekdays[0]]
        if date_phrase == "tuan sau":
            base += timedelta(days=7 - now.weekday() + weekday)
        else:
            base += timedelta(days=(weekday - now.weekday()) % 7 or 7)
    elif date_phrase == "ngay kia":
        base += timedelta(days=2)
    elif date_phrase in {"mai", "ngay mai"}:
        base += timedelta(days=1)
    elif date_phrase == "tuan sau":
        base += timedelta(days=7)
    elif date_phrase == "cuoi thang":
        base = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    for phrase in (*dayparts, *weekdays, *dates):
        value = re.sub(rf"\b{re.escape(phrase)}\b", " ", value)
    value = re.sub(r"\b(?:luc|vao|truoc)\b", " ", value).strip()
    daypart = dayparts[0] if dayparts else None
    hour = {"sang": 8, "trua": 12, "chieu": 14, "toi": 20, "dem": 22}.get(daypart, 8)
    minute = 0
    if value:
        match = re.fullmatch(r"(\d{1,2})(?:(?::|h)\s*(\d{2}))?\s*(?:gio|h)?", value)
        if not match:
            raise ValueError(TIME_GUIDANCE)
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if daypart == "sang":
            if hour > 12:
                raise ValueError(TIME_GUIDANCE)
            hour %= 12
        elif daypart in {"chieu", "trua", "toi", "dem"}:
            if hour == 0 and daypart in {"chieu", "trua"}:
                raise ValueError(TIME_GUIDANCE)
            if 0 < hour < 12:
                hour += 12
            elif hour == 12 and daypart in {"toi", "dem"}:
                hour = 0
    elif not (dayparts or weekdays or dates):
        raise ValueError(TIME_GUIDANCE)
    if hour > 23 or minute > 59:
        raise ValueError(TIME_GUIDANCE)
    return datetime.combine(base.date(), time(hour, minute), tzinfo=tz)
