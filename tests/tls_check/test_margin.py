from datetime import UTC, datetime, timedelta

import pytest

from tls_check import days_remaining, has_enough_margin

NOW = datetime(2026, 10, 15, 9, 17, tzinfo=UTC)


@pytest.mark.parametrize(
    ("not_after", "expected"),
    [
        (NOW + timedelta(days=90), 90),
        (NOW + timedelta(days=14), 14),
        (NOW + timedelta(days=14, seconds=-1), 13),
        (NOW + timedelta(days=13), 13),
        (NOW + timedelta(seconds=1), 0),
        (NOW, 0),
        (NOW - timedelta(seconds=1), -1),
        (NOW - timedelta(days=3), -3),
    ],
)
def test_days_remaining_counts_whole_days_left(not_after: datetime, expected: int) -> None:
    assert days_remaining(not_after, NOW) == expected


@pytest.mark.parametrize(
    ("days", "expected"),
    [(90, True), (15, True), (14, True), (13, False), (0, False), (-1, False)],
)
def test_has_enough_margin_requires_fourteen_days(days: int, expected: bool) -> None:
    assert has_enough_margin(days) is expected
