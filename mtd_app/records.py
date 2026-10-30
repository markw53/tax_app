"""Totals by HMRC category for an income source over a period."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .categories import PROPERTY_FINANCE_COST, SE_DISALLOWED
from .models import IncomeSource, MileageTrip, PropertyOwner, Transaction
from .periods import tax_year_bounds, tax_year_of

# Simplified mileage rates, in tenths of a penny per tenth of a mile
CAR_VAN_FIRST_10K = 45
CAR_VAN_AFTER = 25
MOTORCYCLE = 24
CAR_VAN_BAND_TENTHS = 10_000 * 10


@dataclass
class Totals:
    income: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    expense: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    mileage_tenths: int = 0

    @property
    def total_income(self) -> int:
        return sum(self.income.values())

    @property
    def total_expenses(self) -> int:
        return sum(self.expense.values())


def split_pence(total: int, shares: list[tuple[int, int]]) -> dict[int, int]:
    """Split total between owners by basis points, so the parts always add up
    to the owners' combined share. Leftover pennies go to the largest
    remainders, then to the earliest owner."""
    allocated_bp = sum(bp for _, bp in shares)
    owned = total * allocated_bp // 10_000
    parts = {owner: total * bp // 10_000 for owner, bp in shares}
    remainders = sorted(shares, key=lambda s: -(total * s[1] % 10_000))
    for owner, _ in remainders[: owned - sum(parts.values())]:
        parts[owner] += 1
    return parts


def mileage_allowance_pence(car_van_tenths: int, motorcycle_tenths: int) -> int:
    first = min(car_van_tenths, CAR_VAN_BAND_TENTHS)
    rest = car_van_tenths - first
    tenth_pence = first * CAR_VAN_FIRST_10K + rest * CAR_VAN_AFTER + motorcycle_tenths * MOTORCYCLE
    return (tenth_pence + 5) // 10


def _miles_by_vehicle(session: Session, source_id: int, start: date, end: date) -> dict[str, int]:
    rows = session.execute(
        select(MileageTrip.vehicle, func.sum(MileageTrip.miles_tenths))
        .where(MileageTrip.source_id == source_id,
               MileageTrip.date >= start, MileageTrip.date <= end)
        .group_by(MileageTrip.vehicle)
    ).all()
    return dict(rows)


def _ytd_allowance(session: Session, source_id: int, end: date) -> int:
    year_start, _ = tax_year_bounds(tax_year_of(end))
    miles = _miles_by_vehicle(session, source_id, year_start, end)
    return mileage_allowance_pence(miles.get("car_van", 0), miles.get("motorcycle", 0))


def _self_employment(session: Session, source: IncomeSource, start: date, end: date) -> Totals:
    totals = Totals()
    rows = session.execute(
        select(Transaction.kind, Transaction.category, func.sum(Transaction.amount_pence))
        .where(Transaction.source_id == source.id,
               Transaction.date >= start, Transaction.date <= end)
        .group_by(Transaction.kind, Transaction.category)
    ).all()
    for kind, category, pence in rows:
        getattr(totals, kind)[category] += pence

    miles = _miles_by_vehicle(session, source.id, start, end)
    totals.mileage_tenths = sum(miles.values())
    if source.vehicle_method == "simplified" and totals.mileage_tenths:
        # The 10,000-mile band runs across the tax year, so take the
        # difference of year-to-date allowances rather than pricing the
        # window on its own.
        allowance = _ytd_allowance(session, source.id, end)
        if start > tax_year_bounds(tax_year_of(start))[0]:
            allowance -= _ytd_allowance(session, source.id, start - timedelta(days=1))
        totals.expense["carVanTravelExpenses"] += allowance
    return totals


def _uk_property(session: Session, source: IncomeSource, start: date, end: date) -> Totals:
    totals = Totals()
    holdings = session.scalars(
        select(PropertyOwner).where(PropertyOwner.user_id == source.user_id)
    ).all()
    for holding in holdings:
        shares = [(o.user_id, o.share_bp) for o in sorted(holding.property.owners, key=lambda o: o.id)]
        rows = session.execute(
            select(Transaction.kind, Transaction.category, func.sum(Transaction.amount_pence))
            .where(Transaction.property_id == holding.property_id,
                   Transaction.date >= start, Transaction.date <= end)
            .group_by(Transaction.kind, Transaction.category)
        ).all()
        for kind, category, pence in rows:
            getattr(totals, kind)[category] += split_pence(pence, shares)[source.user_id]
    return totals


def totals_for(session: Session, source: IncomeSource, start: date, end: date) -> Totals:
    if source.type == "self-employment":
        return _self_employment(session, source, start, end)
    return _uk_property(session, source, start, end)


def profit_pence(source: IncomeSource, totals: Totals) -> int:
    """Taxable profit before allowances and adjustments. For property,
    residential finance costs are excluded (they become a tax credit)."""
    excluded = SE_DISALLOWED if source.type == "self-employment" else {PROPERTY_FINANCE_COST}
    allowable = sum(v for k, v in totals.expense.items() if k not in excluded)
    return totals.total_income - allowable
