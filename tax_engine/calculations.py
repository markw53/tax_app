"""Income tax and NI estimates for self-employment profits.

These are estimates only: under MTD, HMRC's own calculation is authoritative.
"""
from .rules import TaxYearRules


def personal_allowance(income: float, rules: TaxYearRules) -> float:
    excess = max(income - rules.allowance_taper_threshold, 0)
    return max(rules.personal_allowance - excess // 2, 0)


def calculate_income_tax(income: float, rules: TaxYearRules) -> float:
    taxable = max(income - personal_allowance(income, rules), 0)
    basic = min(taxable, rules.basic_rate_band)
    higher = max(min(taxable, rules.additional_rate_threshold) - rules.basic_rate_band, 0)
    additional = max(taxable - rules.additional_rate_threshold, 0)
    return (basic * rules.basic_rate
            + higher * rules.higher_rate
            + additional * rules.additional_rate)


def calculate_class4_ni(profit: float, rules: TaxYearRules) -> float:
    main = max(min(profit, rules.class4_upper_limit) - rules.class4_lower_limit, 0)
    upper = max(profit - rules.class4_upper_limit, 0)
    return main * rules.class4_main_rate + upper * rules.class4_upper_rate


def calculate_class2_ni(profit: float, rules: TaxYearRules, weeks: int = 52) -> float:
    # From 2022/23, profits between the small profits threshold and the lower
    # profits limit get Class 2 credited without paying, so it's only due at
    # or above the lower profits limit.
    if rules.class2_weekly is None or profit < rules.class4_lower_limit:
        return 0.0
    return weeks * rules.class2_weekly


def calculate_national_insurance(profit: float, rules: TaxYearRules, weeks: int = 52) -> float:
    return calculate_class4_ni(profit, rules) + calculate_class2_ni(profit, rules, weeks)


def calculate_finance_cost_credit(finance_costs: float, property_profit: float,
                                  total_income: float, rules: TaxYearRules) -> float:
    """Section 24 relief: basic-rate credit on residential finance costs,
    capped at property profits and income above the personal allowance.
    Unused costs carry forward (not tracked here)."""
    above_allowance = max(total_income - personal_allowance(total_income, rules), 0)
    return rules.basic_rate * max(min(finance_costs, max(property_profit, 0), above_allowance), 0)


def estimate_tax(trading_profit: float, property_profit: float,
                 finance_costs: float, rules: TaxYearRules) -> dict[str, float]:
    trading = max(trading_profit, 0)
    prop = max(property_profit, 0)
    total_income = trading + prop
    income_tax = calculate_income_tax(total_income, rules)
    credit = min(calculate_finance_cost_credit(finance_costs, prop, total_income, rules), income_tax)
    class4 = calculate_class4_ni(trading, rules)
    class2 = calculate_class2_ni(trading, rules)
    return {
        "total_income": total_income,
        "income_tax": income_tax,
        "finance_cost_credit": credit,
        "class4_ni": class4,
        "class2_ni": class2,
        "total": income_tax - credit + class4 + class2,
    }
