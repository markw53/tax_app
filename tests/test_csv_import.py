from datetime import date

from mtd_app.csv_import import parse_transactions


def test_unquoted_comma_reports_error_instead_of_crashing():
    rows, errors = parse_transactions(
        b"date,type,category,amount\n2026-05-01,income,turnover,1,000\n", "self-employment")
    assert rows == [] and errors == [
        "Row 2: too many columns. Put values containing commas in quotes."]


def test_missing_columns():
    _, errors = parse_transactions(b"date,amount\n2026-05-01,5\n", "self-employment")
    assert errors == ["Missing column(s): type, category"]


def test_short_row_and_blank_lines():
    rows, errors = parse_transactions(
        b"date,type,category,amount\n\n2026-05-01,income\n", "self-employment")
    assert rows == [] and len(errors) == 1 and errors[0].startswith("Row 3:")


def test_property_categories_and_bom():
    rows, errors = parse_transactions(
        "﻿date,type,category,amount\n06/04/2026,expense,Residential mortgage interest and finance costs,300\n"
        .encode(), "uk-property")
    assert errors == []
    assert rows[0]["date"] == date(2026, 4, 6)
    assert rows[0]["category"] == "residentialFinancialCost"


def test_not_utf8():
    _, errors = parse_transactions(b"\xff\xfe\x00bad", "self-employment")
    assert "UTF-8" in errors[0]
