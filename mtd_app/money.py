from decimal import Decimal, InvalidOperation


def parse_pence(text: str) -> int:
    """'£1,234.56' -> 123456. Rejects negatives and fractions of a penny."""
    cleaned = text.strip().replace("£", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"'{text}' isn't an amount")
    if value < 0:
        raise ValueError("Amounts can't be negative")
    pence = value * 100
    if pence != pence.to_integral_value():
        raise ValueError(f"'{text}' has more than 2 decimal places")
    return int(pence)


def parse_miles_tenths(text: str) -> int:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(f"'{text}' isn't a number of miles")
    tenths = value * 10
    if value <= 0 or tenths != tenths.to_integral_value():
        raise ValueError("Miles must be positive, to at most 1 decimal place")
    return int(tenths)


def format_pence(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    return f"{sign}£{Decimal(abs(pence)) / 100:,.2f}"
