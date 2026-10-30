"""Import transactions from CSV: date, type, category, amount, description.

type is 'income' or 'expense'; category may be the HMRC key or its label.
Dates may be YYYY-MM-DD or DD/MM/YYYY. All rows must be valid or none are
imported.
"""
import csv
import io
from datetime import date, datetime

from .categories import BY_SOURCE_TYPE
from .money import parse_pence

REQUIRED = ("date", "type", "category", "amount")


def _parse_date(text: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f"'{text}' isn't a date (use YYYY-MM-DD or DD/MM/YYYY)")


def _resolve_category(source_type: str, kind: str, text: str) -> str:
    options = BY_SOURCE_TYPE[source_type][kind]
    wanted = text.strip()
    for key, label in options.items():
        if wanted.lower() in (key.lower(), label.lower()):
            return key
    raise ValueError(f"'{text}' isn't {'an income' if kind == 'income' else 'an expense'} category")


def parse_transactions(data: bytes, source_type: str) -> tuple[list[dict], list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["The file isn't UTF-8 text. Save it as CSV (UTF-8) and try again."]
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [h for h in REQUIRED if h not in headers]
    if missing:
        return [], [f"Missing column(s): {', '.join(missing)}"]
    reader.fieldnames = headers

    rows, errors = [], []
    for raw in reader:
        line = reader.line_num  # file line, so blank lines don't shift numbering
        if None in raw:
            errors.append(f"Row {line}: too many columns. Put values containing commas in quotes.")
            continue
        if not any((v or "").strip() for v in raw.values()):
            continue
        try:
            kind = (raw["type"] or "").strip().lower()
            if kind not in ("income", "expense"):
                raise ValueError(f"type must be 'income' or 'expense', not '{raw['type']}'")
            rows.append({
                "date": _parse_date(raw["date"] or ""),
                "kind": kind,
                "category": _resolve_category(source_type, kind, raw["category"] or ""),
                "amount_pence": parse_pence(raw["amount"] or ""),
                "description": (raw.get("description") or "").strip()[:255],
            })
        except ValueError as e:
            errors.append(f"Row {line}: {e}")
    return rows, errors
