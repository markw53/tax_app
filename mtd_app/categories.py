"""HMRC income and expense categories.

Keys match the field names used by HMRC's MTD Self-Employment Business and
Property Business APIs, so totals can be submitted without remapping.
Labels follow the SA103F (self-employment) and SA105 (UK property) forms.
"""

SELF_EMPLOYMENT = {
    "income": {
        "turnover": "Turnover (takings, fees, sales)",
        "other": "Other business income",
    },
    "expense": {
        "costOfGoods": "Cost of goods bought for resale",
        "paymentsToSubcontractors": "Payments to subcontractors",
        "wagesAndStaffCosts": "Wages, salaries and other staff costs",
        "carVanTravelExpenses": "Car, van and travel expenses",
        "premisesRunningCosts": "Rent, rates, power and insurance costs",
        "maintenanceCosts": "Repairs and maintenance of property and equipment",
        "adminCosts": "Phone, fax, stationery and other office costs",
        "businessEntertainmentCosts": "Business entertainment (not allowable)",
        "advertisingCosts": "Advertising and marketing",
        "interestOnBankOtherLoans": "Interest on bank and other loans",
        "financeCharges": "Bank, credit card and other financial charges",
        "irrecoverableDebts": "Irrecoverable debts written off",
        "professionalFees": "Accountancy, legal and other professional fees",
        "depreciation": "Depreciation and loss/profit on sale of assets (not allowable)",
        "otherExpenses": "Other business expenses",
    },
}

UK_PROPERTY = {
    "income": {
        "periodAmount": "Rents received",
        "premiumsOfLeaseGrant": "Premiums for the grant of a lease",
        "reversePremiums": "Reverse premiums and inducements",
        "otherIncome": "Other property income",
    },
    "expense": {
        "premisesRunningCosts": "Rent, rates, insurance and ground rents",
        "repairsAndMaintenance": "Property repairs and maintenance",
        "financialCosts": "Non-residential loan interest and other financial costs",
        "professionalFees": "Legal, management and other professional fees",
        "costOfServices": "Costs of services provided, including wages",
        "travelCosts": "Travel costs",
        "other": "Other allowable property expenses",
        "residentialFinancialCost": "Residential mortgage interest and finance costs",
    },
}

# Not deductible from profit; they reduce tax via other routes.
SE_DISALLOWED = {"businessEntertainmentCosts", "depreciation"}
# Section 24: given as a 20% tax credit, not deducted from property profit.
PROPERTY_FINANCE_COST = "residentialFinancialCost"

BY_SOURCE_TYPE = {
    "self-employment": SELF_EMPLOYMENT,
    "uk-property": UK_PROPERTY,
}


def label(source_type: str, kind: str, category: str) -> str:
    return BY_SOURCE_TYPE[source_type][kind].get(category, category)


def is_valid(source_type: str, kind: str, category: str) -> bool:
    return category in BY_SOURCE_TYPE.get(source_type, {}).get(kind, {})
