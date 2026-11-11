"""HMRC operations that touch the database: tokens, links and submissions."""
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from sqlalchemy import select

from . import annual, hmrc_payloads
from .hmrc_client import HmrcClient, HmrcError
from .hmrc_config import decrypt, encrypt
from .models import AnnualFigures, FinalDeclaration, HmrcConnection, IncomeSource, Submission, User
from .periods import tax_year_bounds
from .records import totals_for

REFRESH_MARGIN = timedelta(minutes=2)


class NotConnected(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def store_tokens(db: Session, user: User, env: str, tokens: dict) -> HmrcConnection:
    conn = user.hmrc or HmrcConnection(user_id=user.id)
    conn.env = env
    conn.access_token = encrypt(tokens["access_token"])
    conn.refresh_token = encrypt(tokens["refresh_token"])
    conn.expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 14400)))
    db.add(conn)
    db.commit()
    db.refresh(user)
    return conn


def disconnect(db: Session, user: User):
    if user.hmrc:
        db.delete(user.hmrc)
        db.commit()
        db.refresh(user)


def access_token(db: Session, user: User, client: HmrcClient) -> str:
    conn = user.hmrc
    if not conn or conn.env != client.config.env:
        raise NotConnected("Connect to HMRC first.")
    if conn.expires_at - REFRESH_MARGIN > utcnow():
        return decrypt(conn.access_token)
    try:
        tokens = client.refresh(decrypt(conn.refresh_token))
    except HmrcError as e:
        if e.status in (400, 401):
            disconnect(db, user)
            raise NotConnected("Your HMRC authorisation has expired. Connect again.")
        raise
    return decrypt(store_tokens(db, user, conn.env, tokens).access_token)


def annual_figures(db: Session, source: IncomeSource, tax_year: str) -> dict:
    """The allowances and adjustments entered for this source and year."""
    row = db.scalar(select(AnnualFigures).where(AnnualFigures.source_id == source.id,
                                                AnnualFigures.tax_year == tax_year))
    return json.loads(row.data) if row else {}


def save_annual_figures(db: Session, source: IncomeSource, tax_year: str, data: dict):
    row = db.scalar(select(AnnualFigures).where(AnnualFigures.source_id == source.id,
                                                AnnualFigures.tax_year == tax_year))
    if row is None:
        row = AnnualFigures(source_id=source.id, tax_year=tax_year)
        db.add(row)
    row.data = json.dumps(data)
    db.commit()


def claims_income_allowance(db: Session, source: IncomeSource, tax_year: str) -> bool:
    return annual.claims_income_allowance(source.type, annual_figures(db, source, tax_year))


def build_update(db: Session, source: IncomeSource, tax_year: str, period_end: date) -> dict:
    start, year_end = tax_year_bounds(tax_year)
    if not start <= period_end <= year_end:
        raise ValueError(f"{period_end} isn't in tax year {tax_year}")
    totals = totals_for(db, source, start, period_end)
    # Claiming the trading or property allowance means claiming no expenses
    with_expenses = not claims_income_allowance(db, source, tax_year)
    if source.type == "self-employment":
        return hmrc_payloads.self_employment(totals, start, period_end, with_expenses)
    return hmrc_payloads.uk_property(totals, start, period_end, with_expenses)


def build_annual(db: Session, source: IncomeSource, tax_year: str) -> dict:
    """The annual submission body. Raises ValueError if it can't be sent."""
    annual.check_tax_year(tax_year)
    data = annual_figures(db, source, tax_year)
    body = annual.payload(source.type, data)
    allowance = data.get(annual.INCOME_ALLOWANCE[source.type])
    if allowance:
        income = totals_for(db, source, *tax_year_bounds(tax_year)).total_income
        if allowance > income:
            raise ValueError(f"The allowance can't be more than your income for the year "
                             f"(£{income / 100:,.2f}).")
    return body


def _require_link(user: User, source: IncomeSource):
    if not user.nino:
        raise ValueError("Add your National Insurance number first.")
    if not source.hmrc_business_id:
        raise ValueError(f"Link '{source.name}' to its HMRC business first.")


def _send(db: Session, record, call):
    """Run an HMRC call and record how it went on the audit row."""
    try:
        resp = call()
    except HmrcError as e:
        record.status_code, record.error = e.status, e.describe()
        record.correlation_id = e.correlation_id
    else:
        record.status_code, record.succeeded = resp.status_code, True
        record.correlation_id = resp.headers.get("X-CorrelationId")
    db.add(record)
    db.commit()
    return record


def submit_annual(db: Session, user: User, source: IncomeSource, tax_year: str,
                  client: HmrcClient, fraud: dict[str, str]) -> Submission:
    _require_link(user, source)
    body = build_annual(db, source, tax_year)
    token = access_token(db, user, client)
    start, end = tax_year_bounds(tax_year)
    record = Submission(user_id=user.id, source_id=source.id, env=client.config.env, kind="annual",
                        tax_year=tax_year, period_start=start, period_end=end,
                        payload=json.dumps(body, indent=2))
    submit = (client.submit_self_employment_annual if source.type == "self-employment"
              else client.submit_uk_property_annual)
    return _send(db, record, lambda: submit(token, fraud, user.nino, source.hmrc_business_id,
                                            tax_year, body))


def submit_final_declaration(db: Session, user: User, tax_year: str, calculation_id: str,
                             client: HmrcClient, fraud: dict[str, str]) -> FinalDeclaration:
    if not user.nino:
        raise ValueError("Add your National Insurance number first.")
    token = access_token(db, user, client)
    record = FinalDeclaration(user_id=user.id, env=client.config.env, tax_year=tax_year,
                              calculation_id=calculation_id)
    return _send(db, record, lambda: client.submit_final_declaration(
        token, fraud, user.nino, tax_year, calculation_id))


def submit_update(db: Session, user: User, source: IncomeSource, tax_year: str, period_end: date,
                  client: HmrcClient, fraud: dict[str, str]) -> Submission:
    _require_link(user, source)
    body = build_update(db, source, tax_year, period_end)
    token = access_token(db, user, client)

    record = Submission(user_id=user.id, source_id=source.id, env=client.config.env,
                        tax_year=tax_year, period_start=tax_year_bounds(tax_year)[0],
                        period_end=period_end, payload=json.dumps(body, indent=2))
    submit = (client.submit_self_employment if source.type == "self-employment"
              else client.submit_uk_property)
    return _send(db, record, lambda: submit(token, fraud, user.nino, source.hmrc_business_id,
                                            tax_year, body))
