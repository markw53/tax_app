import pytest

from tax_engine import (
    TAX_YEARS,
    calculate_class2_ni,
    calculate_class4_ni,
    calculate_income_tax,
    personal_allowance,
)

Y2526 = TAX_YEARS["2025/26"]


@pytest.mark.parametrize("income, expected", [
    (0, 0),
    (10_000, 0),
    (12_570, 0),
    (30_000, 3_486),       # 17,430 @ 20%
    (60_000, 11_432),      # 37,700 @ 20% + 9,730 @ 40%
    (110_000, 33_432),     # PA tapered to 7,570; basic band stays 37,700
    (125_140, 42_516),     # PA fully withdrawn
    (150_000, 53_703),     # 24,860 @ 45% on top
])
def test_income_tax_2025_26(income, expected):
    assert calculate_income_tax(income, Y2526) == pytest.approx(expected)


def test_basic_band_does_not_grow_when_allowance_tapers():
    # Regression: the old code used basic_limit - allowance, which gave a
    # £42,700 basic band at £110k and understated tax by £1,000.
    assert calculate_income_tax(110_000, Y2526) == pytest.approx(33_432)


@pytest.mark.parametrize("income, expected", [
    (100_000, 12_570),
    (100_001, 12_570),
    (110_000, 7_570),
    (125_140, 0),
    (200_000, 0),
])
def test_personal_allowance_taper(income, expected):
    assert personal_allowance(income, Y2526) == expected


@pytest.mark.parametrize("profit, expected", [
    (12_570, 0),
    (30_000, 1_045.80),
    (60_000, 2_456.60),    # 37,700 @ 6% + 9,730 @ 2%
])
def test_class4_ni_2025_26(profit, expected):
    assert calculate_class4_ni(profit, Y2526) == pytest.approx(expected)


def test_class4_ni_2022_23_uses_annualised_limit_and_blended_rate():
    assert calculate_class4_ni(20_000, TAX_YEARS["2022/23"]) == pytest.approx(8_092 * 0.0973)


@pytest.mark.parametrize("year", ["2024/25", "2025/26", "2026/27"])
def test_no_class2_from_2024_25(year):
    assert calculate_class2_ni(40_000, TAX_YEARS[year]) == 0


@pytest.mark.parametrize("profit, expected", [
    (5_000, 0),
    (10_000, 0),           # between SPT and LPL: credited, not paid
    (20_000, 52 * 3.45),
])
def test_class2_ni_2023_24(profit, expected):
    assert calculate_class2_ni(profit, TAX_YEARS["2023/24"]) == pytest.approx(expected)


def test_every_year_has_rules():
    assert set(TAX_YEARS) >= {"2022/23", "2023/24", "2024/25", "2025/26", "2026/27"}
