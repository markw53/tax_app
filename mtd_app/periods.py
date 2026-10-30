"""UK tax years and MTD quarterly update periods."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Quarter:
    number: int
    start: date
    end: date
    deadline: date

    @property
    def label(self) -> str:
        return f"Q{self.number}: {self.start:%-d %b} – {self.end:%-d %b %Y}"


def tax_year_of(d: date) -> str:
    start_year = d.year if d >= date(d.year, 4, 6) else d.year - 1
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def tax_year_start_year(tax_year: str) -> int:
    return int(tax_year.split("/")[0])


def tax_year_bounds(tax_year: str) -> tuple[date, date]:
    y = tax_year_start_year(tax_year)
    return date(y, 4, 6), date(y + 1, 4, 5)


def quarters(tax_year: str, calendar: bool = False) -> list[Quarter]:
    """Quarterly update periods. Deadlines are the 7th of the month after
    the standard quarter end, whichever basis is used."""
    y = tax_year_start_year(tax_year)
    if calendar:
        ends = [date(y, 6, 30), date(y, 9, 30), date(y, 12, 31), date(y + 1, 4, 5)]
    else:
        ends = [date(y, 7, 5), date(y, 10, 5), date(y + 1, 1, 5), date(y + 1, 4, 5)]
    deadlines = [date(y, 8, 7), date(y, 11, 7), date(y + 1, 2, 7), date(y + 1, 5, 7)]
    starts = [date(y, 4, 6)] + [date.fromordinal(e.toordinal() + 1) for e in ends[:-1]]
    return [Quarter(i + 1, s, e, dl) for i, (s, e, dl) in enumerate(zip(starts, ends, deadlines))]
