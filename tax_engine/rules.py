"""Tax-year parameters for UK (rest of UK, not Scottish) self-employed taxpayers."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TaxYearRules:
    # Income tax
    personal_allowance: float
    basic_rate_band: float            # width of the basic-rate band (on taxable income)
    additional_rate_threshold: float  # taxable income above which additional rate applies
    basic_rate: float
    higher_rate: float
    additional_rate: float
    allowance_taper_threshold: float  # PA reduced by £1 for every £2 of income above this

    # Class 4 NI
    class4_lower_limit: float
    class4_upper_limit: float
    class4_main_rate: float
    class4_upper_rate: float

    # Class 2 NI: None from 2024/25, when compulsory Class 2 was abolished
    class2_weekly: Optional[float] = None


TAX_YEARS = {
    "2022/23": TaxYearRules(
        personal_allowance=12570,
        basic_rate_band=37700,
        additional_rate_threshold=125140,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        allowance_taper_threshold=100000,
        # Annualised LPL and blended rate (10.25% to 5 Nov 2022, 9% after)
        class4_lower_limit=11908,
        class4_upper_limit=50270,
        class4_main_rate=0.0973,
        class4_upper_rate=0.0273,
        class2_weekly=3.15,
    ),
    "2023/24": TaxYearRules(
        personal_allowance=12570,
        basic_rate_band=37700,
        additional_rate_threshold=125140,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        allowance_taper_threshold=100000,
        class4_lower_limit=12570,
        class4_upper_limit=50270,
        class4_main_rate=0.09,
        class4_upper_rate=0.02,
        class2_weekly=3.45,
    ),
    "2024/25": TaxYearRules(
        personal_allowance=12570,
        basic_rate_band=37700,
        additional_rate_threshold=125140,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        allowance_taper_threshold=100000,
        class4_lower_limit=12570,
        class4_upper_limit=50270,
        class4_main_rate=0.06,
        class4_upper_rate=0.02,
    ),
    "2025/26": TaxYearRules(
        personal_allowance=12570,
        basic_rate_band=37700,
        additional_rate_threshold=125140,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        allowance_taper_threshold=100000,
        class4_lower_limit=12570,
        class4_upper_limit=50270,
        class4_main_rate=0.06,
        class4_upper_rate=0.02,
    ),
    "2026/27": TaxYearRules(
        personal_allowance=12570,
        basic_rate_band=37700,
        additional_rate_threshold=125140,
        basic_rate=0.20,
        higher_rate=0.40,
        additional_rate=0.45,
        allowance_taper_threshold=100000,
        class4_lower_limit=12570,
        class4_upper_limit=50270,
        class4_main_rate=0.06,
        class4_upper_rate=0.02,
    ),
}
