"""Request bodies for HMRC's cumulative quarterly update endpoints.

Updates are cumulative: each one carries year-to-date totals, from the start
of the tax year to the end of the quarter being reported.
"""

from datetime import date

from .categories import (
    SE_DISALLOWED,
    SELF_EMPLOYMENT,
    UK_PROPERTY,
)
from .records import Totals


def _pounds(pence: int) -> float:
    return round(pence / 100, 2)


def self_employment(totals: Totals, start: date, end: date) -> dict:
    expenses = {
        k: _pounds(v)
        for k, v in totals.expense.items()
        if v and k in SELF_EMPLOYMENT["expense"]
    }
    body = {
        "periodDates": {
            "periodStartDate": start.isoformat(),
            "periodEndDate": end.isoformat(),
        },
        "periodIncome": {
            k: _pounds(totals.income.get(k, 0)) for k in SELF_EMPLOYMENT["income"]
        },
    }
    if expenses:
        body["periodExpenses"] = expenses
    # Entertainment and depreciation are reported as expenses, then added
    # back in full as disallowable.
    disallowed = {
        f"{k}Disallowable": expenses[k] for k in sorted(SE_DISALLOWED) if k in expenses
    }
    if disallowed:
        body["periodDisallowableExpenses"] = disallowed
    return body


def uk_property(totals: Totals, start: date, end: date) -> dict:
    income = {
        k: _pounds(v)
        for k, v in totals.income.items()
        if v and k in UK_PROPERTY["income"]
    }
    expenses = {
        k: _pounds(v)
        for k, v in totals.expense.items()
        if v and k in UK_PROPERTY["expense"]
    }
    section = {"income": income or {"periodAmount": 0.0}}
    if expenses:
        section["expenses"] = expenses
    return {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "ukProperty": section,
    }
