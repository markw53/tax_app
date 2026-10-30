from datetime import date

import pytest

from mtd_app.models import (
    IncomeSource, MileageTrip, Property, PropertyOwner, Transaction, User,
)
from mtd_app.records import (
    mileage_allowance_pence, profit_pence, split_pence, totals_for,
)

YEAR_START, YEAR_END = date(2026, 4, 6), date(2027, 4, 5)


@pytest.mark.parametrize("total, shares, expected", [
    (101, [(1, 5000), (2, 5000)], {1: 51, 2: 50}),
    (100, [(1, 3333), (2, 6667)], {1: 33, 2: 67}),
    (0, [(1, 5000), (2, 5000)], {1: 0, 2: 0}),
    (101, [(1, 5000)], {1: 50}),       # co-owner not a user of the app
    (100, [(1, 10000)], {1: 100}),
])
def test_split_pence(total, shares, expected):
    assert split_pence(total, shares) == expected


def test_mileage_allowance_band():
    assert mileage_allowance_pence(10_000 * 10, 0) == 450_000
    assert mileage_allowance_pence(12_000 * 10, 0) == 500_000
    assert mileage_allowance_pence(0, 100 * 10) == 2_400
    assert mileage_allowance_pence(1, 0) == 5   # 0.1 mile = 4.5p, rounds half up


def _user(session, email):
    u = User(email=email, name=email.split("@")[0], password_hash="x")
    session.add(u)
    session.flush()
    return u


def test_self_employment_totals_and_simplified_mileage(session):
    u = _user(session, "driver@example.com")
    src = IncomeSource(user=u, type="self-employment", name="Delivery", vehicle_method="simplified")
    session.add(src)
    session.flush()
    session.add_all([
        Transaction(date=date(2026, 5, 1), kind="income", category="turnover",
                    amount_pence=1_000_000, source_id=src.id, created_by_id=u.id),
        Transaction(date=date(2026, 5, 2), kind="expense", category="adminCosts",
                    amount_pence=3_000, source_id=src.id, created_by_id=u.id),
        Transaction(date=date(2026, 5, 3), kind="expense", category="depreciation",
                    amount_pence=50_000, source_id=src.id, created_by_id=u.id),
        MileageTrip(source_id=src.id, date=date(2026, 6, 1), miles_tenths=9_000 * 10),
        MileageTrip(source_id=src.id, date=date(2026, 8, 1), miles_tenths=2_000 * 10),
    ])
    session.flush()

    q1 = totals_for(session, src, YEAR_START, date(2026, 7, 5))
    assert q1.income == {"turnover": 1_000_000}
    assert q1.expense["carVanTravelExpenses"] == 405_000

    # Q2 alone crosses the 10,000-mile band: 1,000 @ 45p + 1,000 @ 25p
    q2 = totals_for(session, src, date(2026, 7, 6), date(2026, 10, 5))
    assert q2.expense["carVanTravelExpenses"] == 70_000

    ytd = totals_for(session, src, YEAR_START, date(2026, 10, 5))
    assert ytd.expense["carVanTravelExpenses"] == 475_000
    # depreciation isn't allowable
    assert profit_pence(src, ytd) == 1_000_000 - 3_000 - 475_000


def test_actual_vehicle_method_ignores_mileage(session):
    u = _user(session, "a@example.com")
    src = IncomeSource(user=u, type="self-employment", name="X", vehicle_method="actual")
    session.add(src)
    session.flush()
    session.add(MileageTrip(source_id=src.id, date=date(2026, 6, 1), miles_tenths=1000))
    session.flush()
    assert "carVanTravelExpenses" not in totals_for(session, src, YEAR_START, YEAR_END).expense


def test_joint_property_split_between_owners(session):
    a, b = _user(session, "a@example.com"), _user(session, "b@example.com")
    src_a = IncomeSource(user=a, type="uk-property", name="Property")
    src_b = IncomeSource(user=b, type="uk-property", name="Property")
    prop = Property(name="1 High St")
    session.add_all([src_a, src_b, prop])
    session.flush()
    session.add_all([
        PropertyOwner(property_id=prop.id, user_id=a.id, share_bp=5000),
        PropertyOwner(property_id=prop.id, user_id=b.id, share_bp=5000),
        Transaction(date=date(2026, 5, 1), kind="income", category="periodAmount",
                    amount_pence=100_101, property_id=prop.id, created_by_id=a.id),
        Transaction(date=date(2026, 5, 1), kind="expense", category="residentialFinancialCost",
                    amount_pence=20_000, property_id=prop.id, created_by_id=a.id),
        Transaction(date=date(2026, 5, 1), kind="expense", category="repairsAndMaintenance",
                    amount_pence=10_000, property_id=prop.id, created_by_id=b.id),
    ])
    session.flush()

    ta = totals_for(session, src_a, YEAR_START, YEAR_END)
    tb = totals_for(session, src_b, YEAR_START, YEAR_END)
    assert ta.income["periodAmount"] + tb.income["periodAmount"] == 100_101
    assert ta.income["periodAmount"] == 50_051
    assert ta.expense["residentialFinancialCost"] == 10_000
    # finance costs aren't deducted from property profit (Section 24)
    assert profit_pence(src_a, ta) == 50_051 - 5_000


def test_transactions_outside_period_excluded(session):
    u = _user(session, "a@example.com")
    src = IncomeSource(user=u, type="self-employment", name="X")
    session.add(src)
    session.flush()
    session.add(Transaction(date=date(2026, 4, 5), kind="income", category="turnover",
                            amount_pence=100, source_id=src.id, created_by_id=u.id))
    session.flush()
    assert totals_for(session, src, YEAR_START, YEAR_END).total_income == 0
