from datetime import date

import pytest

from mtd_app.periods import quarters, tax_year_bounds, tax_year_of


@pytest.mark.parametrize("d, expected", [
    (date(2026, 4, 5), "2025/26"),
    (date(2026, 4, 6), "2026/27"),
    (date(2027, 1, 1), "2026/27"),
    (date(2099, 12, 31), "2099/00"),
])
def test_tax_year_of(d, expected):
    assert tax_year_of(d) == expected


def test_tax_year_bounds():
    assert tax_year_bounds("2026/27") == (date(2026, 4, 6), date(2027, 4, 5))


def test_standard_quarters():
    qs = quarters("2026/27")
    assert [(q.start, q.end, q.deadline) for q in qs] == [
        (date(2026, 4, 6), date(2026, 7, 5), date(2026, 8, 7)),
        (date(2026, 7, 6), date(2026, 10, 5), date(2026, 11, 7)),
        (date(2026, 10, 6), date(2027, 1, 5), date(2027, 2, 7)),
        (date(2027, 1, 6), date(2027, 4, 5), date(2027, 5, 7)),
    ]


def test_calendar_quarters():
    qs = quarters("2026/27", calendar=True)
    assert [(q.start, q.end) for q in qs] == [
        (date(2026, 4, 6), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 12, 31)),
        (date(2027, 1, 1), date(2027, 4, 5)),
    ]
    assert qs[0].deadline == date(2026, 8, 7)
