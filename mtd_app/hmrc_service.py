"""HMRC operations that touch the database: tokens, links and submissions."""
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import hmrc_payloads
from .hmrc_client import HmrcClient, HmrcError
from .hmrc_config import decrypt, encrypt
from .models import HmrcConnection, IncomeSource, Submission, User
from .periods import tax_year_bounds
from .records import totals_for

REFRESH_MARGIN = timedelta(minutes=2)


class NotConnected(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def build_update(db: Session, source: IncomeSource, tax_year: str, period_end: date) -> dict:
    start, year_end = tax_year_bounds(tax_year)
    if not start <= period_end <= year_end:
        raise ValueError(f"{period_end} isn't in tax year {tax_year}")
    totals = totals_for(db, source, start, period_end)
    if source.type == "self-employment":
        return hmrc_payloads.self_employment(totals, start, period_end)
    return hmrc_payloads.uk_property(totals, start, period_end)


def submit_update(db: Session, user: User, source: IncomeSource, tax_year: str, period_end: date,
                  client: HmrcClient, fraud: dict[str, str]) -> Submission:
    if not user.nino:
        raise ValueError("Add your National Insurance number first.")
    if not source.hmrc_business_id:
        raise ValueError(f"Link '{source.name}' to its HMRC business first.")
    body = build_update(db, source, tax_year, period_end)
    token = access_token(db, user, client)

    record = Submission(user_id=user.id, source_id=source.id, env=client.config.env,
                        tax_year=tax_year, period_start=tax_year_bounds(tax_year)[0],
                        period_end=period_end, payload=json.dumps(body, indent=2))
    submit = (client.submit_self_employment if source.type == "self-employment"
              else client.submit_uk_property)
    try:
        resp = submit(token, fraud, user.nino, source.hmrc_business_id, tax_year, body)
    except HmrcError as e:
        record.status_code, record.error = e.status, e.describe()
        record.correlation_id = e.correlation_id
    else:
        record.status_code, record.succeeded = resp.status_code, True
        record.correlation_id = resp.headers.get("X-CorrelationId")
    db.add(record)
    db.commit()
    return record
