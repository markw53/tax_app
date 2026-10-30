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
