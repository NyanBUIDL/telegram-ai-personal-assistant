from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tg_assistant.services.timeparse import parse_vietnamese_datetime

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 29, 10, 30, tzinfo=TZ)  # Thursday


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("9 giờ tối mai", datetime(2026, 10, 30, 21, tzinfo=TZ)),
        ("9h tối mai", datetime(2026, 10, 30, 21, tzinfo=TZ)),
        ("tối mai", datetime(2026, 10, 30, 20, tzinfo=TZ)),
        ("12 giờ trưa mai", datetime(2026, 10, 30, 12, tzinfo=TZ)),
        ("mai lúc 21:45", datetime(2026, 10, 30, 21, 45, tzinfo=TZ)),
        ("mai", datetime(2026, 10, 30, 8, tzinfo=TZ)),
        ("sáng thứ 2", datetime(2026, 11, 2, 8, tzinfo=TZ)),
        ("thứ Hai tuần sau lúc 9 giờ", datetime(2026, 11, 2, 9, tzinfo=TZ)),
    ],
)
def test_supported_calendar_phrases_are_unambiguous(phrase, expected):
    assert parse_vietnamese_datetime(phrase, now=NOW) == expected


@pytest.mark.parametrize(
    "phrase",
    ["", "xyz", "xyz mai", "sau 2 giờ", "9h30abc mai", "24 giờ mai", "8:99 mai", "19 giờ sáng mai", "sáng tối mai", "mai hôm nay"],
)
def test_unknown_or_conflicting_time_is_rejected_with_recovery_guidance(phrase):
    with pytest.raises(ValueError, match="Ví dụ"):
        parse_vietnamese_datetime(phrase, now=NOW)
