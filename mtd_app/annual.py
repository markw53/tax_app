"""End-of-year allowances and adjustments (HMRC's "annual submission").

Field keys match HMRC's Self Employment Business 5.0 and Property Business
6.0 annual submission schemas for tax year 2025/26 onwards. Only the fields
a sole trader or small landlord is likely to need are offered.
"""
from dataclasses import dataclass

from .money import parse_pence
from .periods import tax_year_start_year

FIRST_TAX_YEAR = 2025  # earlier years use different HMRC schemas
INCOME_ALLOWANCE_MAX = 100_000  # £1,000 trading / property allowance

CLASS4_EXEMPTIONS = {
    "": "Not exempt",
    "over-state-pension-age": "Over State Pension age at the start of the tax year",
    "under-16": "Under 16 at the start of the tax year",
    "non-resident": "Not resident in the UK",
    "trustee": "Trustee, executor or administrator",
    "diver": "Diver or diving supervisor",
    "ITTOIA-2005": "Exempt under ITTOIA 2005",
}


@dataclass(frozen=True)
class Field:
    key: str
    section: str  # allowances | adjustments | nonFinancials
    label: str
    hint: str = ""
    kind: str = "money"  # money | bool | choice
    max_pence: int | None = None


SELF_EMPLOYMENT = [
    Field("tradingIncomeAllowance", "allowances", "Trading allowance",
          "Up to £1,000, claimed instead of your expenses and any other allowance.",
          max_pence=INCOME_ALLOWANCE_MAX),
    Field("annualInvestmentAllowance", "allowances", "Annual Investment Allowance",
          "The cost of equipment bought for the business this year. Not cars."),
    Field("capitalAllowanceMainPool", "allowances", "Capital allowances: main pool",
          "Writing down allowance on equipment, including cars with lower CO2 emissions."),
    Field("capitalAllowanceSpecialRatePool", "allowances", "Capital allowances: special rate pool",
          "Writing down allowance at the special rate, including cars with higher CO2 emissions."),
    Field("capitalAllowanceSingleAssetPool", "allowances", "Capital allowances: single asset pools",
          "Allowances on assets kept in their own pool, such as one you also use privately."),
    Field("zeroEmissionsCarAllowance", "allowances", "Zero-emission car allowance"),
    Field("allowanceOnSales", "allowances", "Balancing allowance",
          "When you sold an asset, or stopped using it, for less than its tax value."),
    Field("balancingChargeOther", "adjustments", "Balancing charge",
          "When you sold an asset, or stopped using it, for more than its tax value."),
    Field("goodsAndServicesOwnUse", "adjustments", "Goods and services for your own use",
          "The normal sale price of anything you took out of the business for yourself."),
    Field("includedNonTaxableProfits", "adjustments", "Income that isn't taxable as business profit",
          "Amounts in your records that aren't taxable as profits of this business."),
    Field("class4NicsExemptionReason", "nonFinancials", "Class 4 National Insurance exemption",
          kind="choice"),
]

UK_PROPERTY = [
    Field("propertyIncomeAllowance", "allowances", "Property allowance",
          "Up to £1,000, claimed instead of your expenses and any other allowance. "
          "Each joint owner has their own.", max_pence=INCOME_ALLOWANCE_MAX),
    Field("costOfReplacingDomesticItems", "allowances", "Replacing domestic items",
          "Like-for-like replacement of furniture, appliances and furnishings in a residential let."),
    Field("annualInvestmentAllowance", "allowances", "Annual Investment Allowance",
          "The cost of equipment bought for the property business this year. Not cars."),
    Field("otherCapitalAllowance", "allowances", "Other capital allowances"),
    Field("zeroEmissionsCarAllowance", "allowances", "Zero-emission car allowance"),
    Field("privateUseAdjustment", "adjustments", "Private use adjustment",
          "The part of the expenses in your records that wasn't for the property business."),
    Field("balancingCharge", "adjustments", "Balancing charge",
          "When you sold an asset you'd claimed capital allowances on, or stopped using it."),
    Field("nonResidentLandlord", "adjustments", "I'm a non-resident landlord", kind="bool"),
]

FIELDS = {"self-employment": SELF_EMPLOYMENT, "uk-property": UK_PROPERTY}
INCOME_ALLOWANCE = {"self-employment": "tradingIncomeAllowance",
                    "uk-property": "propertyIncomeAllowance"}


def check_tax_year(tax_year: str):
    if tax_year_start_year(tax_year) < FIRST_TAX_YEAR:
        raise ValueError("Allowances and adjustments can only be sent from here for 2025/26 onwards.")


def claims_income_allowance(source_type: str, data: dict) -> bool:
    """True if the £1,000 trading or property allowance is claimed, which
    replaces all expenses."""
    return bool(data.get(INCOME_ALLOWANCE[source_type]))


def parse_form(source_type: str, form) -> dict:
    """Form values -> {field: pence | bool | str}, leaving out blanks and
    zeros. Raises ValueError with a message for the user."""
    data = {}
    for f in FIELDS[source_type]:
        raw = str(form.get(f.key, "")).strip()
        if f.kind == "bool":
            if raw:
                data[f.key] = True
        elif f.kind == "choice":
            if raw not in CLASS4_EXEMPTIONS:
                raise ValueError("Choose a Class 4 exemption reason from the list.")
            if raw:
                data[f.key] = raw
        elif raw:
            try:
                pence = parse_pence(raw)
            except ValueError as e:
                raise ValueError(f"{f.label}: {e}") from None
            if f.max_pence is not None and pence > f.max_pence:
                raise ValueError(f"{f.label} can't be more than £{f.max_pence // 100:,}.")
            if pence:
                data[f.key] = pence
    validate(source_type, data)
    return data


def validate(source_type: str, data: dict):
    allowance = INCOME_ALLOWANCE[source_type]
    if data.get(allowance):
        label = next(f.label for f in FIELDS[source_type] if f.key == allowance)
        others = [f.label for f in FIELDS[source_type]
                  if f.section == "allowances" and f.key != allowance and data.get(f.key)]
        if others:
            raise ValueError(f"The {label.lower()} can't be claimed with other allowances "
                             f"({', '.join(others)}). Claim one or the other.")
        if source_type == "uk-property" and data.get("privateUseAdjustment"):
            raise ValueError("A private use adjustment can't be sent with the property allowance.")


def payload(source_type: str, data: dict) -> dict:
    """The request body for HMRC. Raises ValueError if there's nothing to send."""
    validate(source_type, data)
    sections: dict[str, dict] = {}
    for f in FIELDS[source_type]:
        value = data.get(f.key)
        if f.kind == "money":
            if value:
                sections.setdefault(f.section, {})[f.key] = round(value / 100, 2)
        elif f.kind == "choice":
            if value:
                sections.setdefault(f.section, {})[f.key] = value
    if source_type == "uk-property":
        # HMRC requires this flag whenever adjustments are sent, and a body
        # with nothing in it is rejected, so it's always included.
        sections.setdefault("adjustments", {})["nonResidentLandlord"] = bool(data.get("nonResidentLandlord"))
        return {"ukProperty": sections}
    if not sections:
        raise ValueError("There's nothing to send: enter at least one figure.")
    return sections


def lines(source_type: str, data: dict) -> list[tuple[str, str]]:
    """(label, display value) for each figure that will be sent."""
    out = []
    for f in FIELDS[source_type]:
        value = data.get(f.key)
        if f.kind == "money" and value:
            out.append((f.label, f"£{value / 100:,.2f}"))
        elif f.kind == "choice" and value:
            out.append((f.label, CLASS4_EXEMPTIONS[value]))
        elif f.kind == "bool" and (value or f.key == "nonResidentLandlord"):
            out.append((f.label.replace("I'm a non-resident", "Non-resident"), "Yes" if value else "No"))
    return out
