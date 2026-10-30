import re
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from tax_engine import TAX_YEARS, estimate_tax

from . import categories
from .auth import authenticate
from .csv_import import parse_transactions
from .db import SessionLocal, secret_key
from .models import IncomeSource, MileageTrip, Property, PropertyOwner, Transaction, User
from .money import format_pence, parse_miles_tenths, parse_pence
from .periods import quarters, tax_year_bounds, tax_year_of
from .categories import PROPERTY_FINANCE_COST
from .records import mileage_allowance_pence, profit_pence, totals_for

MAX_UPLOAD_BYTES = 2 * 1024 * 1024

app = FastAPI(title="MTD records")
app.add_middleware(SessionMiddleware, secret_key=secret_key(), same_site="lax",
                   max_age=60 * 60 * 12)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = format_pence
templates.env.globals["category_label"] = categories.label
templates.env.globals["categories"] = categories.BY_SOURCE_TYPE


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _to_login(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def get_db():
    with SessionLocal() as session:
        yield session


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise LoginRequired()
    return user


DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, template: str, **ctx):
    ctx["flashes"] = request.session.pop("flash", [])
    return templates.TemplateResponse(request, template, ctx)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def pick_year(year: str | None) -> str:
    if year and re.fullmatch(r"\d{4}/\d{2}", year):
        return year
    return tax_year_of(date.today())


def year_options(current: str) -> list[str]:
    return sorted(set(TAX_YEARS) | {current, tax_year_of(date.today())})


def own_source(db: Session, user: User, source_id: int) -> IncomeSource:
    source = db.get(IncomeSource, source_id)
    if not source or source.user_id != user.id:
        raise HTTPException(404)
    return source


def own_property(db: Session, user: User, property_id: int) -> Property:
    prop = db.get(Property, property_id)
    if not prop or user.id not in {o.user_id for o in prop.owners}:
        raise HTTPException(404)
    return prop


# ---------------------------------------------------------------- login

@app.get("/login")
def login_form(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, db: DB, email: Annotated[str, Form()], password: Annotated[str, Form()]):
    user = authenticate(db, email, password)
    if not user:
        flash(request, "Email or password not recognised.", "error")
        return redirect("/login")
    request.session.clear()
    request.session["user_id"] = user.id
    return redirect("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ------------------------------------------------------------ dashboard

@app.get("/")
def dashboard(request: Request, db: DB, user: CurrentUser, year: str | None = None):
    year = pick_year(year)
    year_start, year_end = tax_year_bounds(year)
    qs = quarters(year, calendar=user.calendar_quarters)

    summaries = []
    trading = prop_profit = finance = 0
    for source in sorted(user.sources, key=lambda s: (s.type, s.name)):
        rows = []
        for q in qs:
            t = totals_for(db, source, q.start, q.end)
            rows.append({"quarter": q, "income": t.total_income, "expenses": t.total_expenses,
                         "profit": profit_pence(source, t)})
        ytd = totals_for(db, source, year_start, year_end)
        profit = profit_pence(source, ytd)
        if source.type == "self-employment":
            trading += profit
        else:
            prop_profit += profit
            finance += ytd.expense.get(PROPERTY_FINANCE_COST, 0)
        summaries.append({"source": source, "rows": rows, "ytd": ytd, "profit": profit})

    rules = TAX_YEARS.get(year)
    estimate = None
    if rules:
        est = estimate_tax(trading / 100, prop_profit / 100, finance / 100, rules)
        estimate = {k: round(v * 100) for k, v in est.items()}

    properties = [h.property for h in user.property_shares]
    return render(request, "dashboard.html", user=user, year=year, years=year_options(year),
                  summaries=summaries, estimate=estimate, properties=properties,
                  today=date.today())


# -------------------------------------------------------- self-employment

@app.get("/sources/{source_id}")
def source_page(request: Request, db: DB, user: CurrentUser, source_id: int, year: str | None = None):
    source = own_source(db, user, source_id)
    if source.type == "uk-property":
        return redirect(f"/?year={pick_year(year)}")
    year = pick_year(year)
    start, end = tax_year_bounds(year)
    txns = db.scalars(
        select(Transaction).where(Transaction.source_id == source.id,
                                  Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    ).all()
    trips = db.scalars(
        select(MileageTrip).where(MileageTrip.source_id == source.id,
                                  MileageTrip.date >= start, MileageTrip.date <= end)
        .order_by(MileageTrip.date.desc(), MileageTrip.id.desc())
    ).all()
    car = sum(t.miles_tenths for t in trips if t.vehicle == "car_van")
    moto = sum(t.miles_tenths for t in trips if t.vehicle == "motorcycle")
    totals = totals_for(db, source, start, end)
    return render(request, "source.html", user=user, source=source, year=year,
                  years=year_options(year), txns=txns, trips=trips, totals=totals,
                  miles_tenths=car + moto, allowance=mileage_allowance_pence(car, moto),
                  today=date.today(), source_type=source.type,
                  post_url=f"/sources/{source.id}")


def _add_transaction(request, db, user, source_type, date_, entry, amount, description, **owner):
    try:
        kind, category = entry.split(":", 1)
        if not categories.is_valid(source_type, kind, category):
            raise ValueError("Choose a category")
        pence = parse_pence(amount)
        if pence == 0:
            raise ValueError("Enter an amount")
    except ValueError as e:
        flash(request, str(e), "error")
        return
    db.add(Transaction(date=date_, kind=kind, category=category, amount_pence=pence,
                       description=description.strip()[:255], created_by_id=user.id, **owner))
    db.commit()
    flash(request, f"Added {format_pence(pence)} {categories.label(source_type, kind, category).lower()}.")


@app.post("/sources/{source_id}/transactions")
def add_source_transaction(request: Request, db: DB, user: CurrentUser, source_id: int,
                           date_: Annotated[date, Form(alias="date")],
                           entry: Annotated[str, Form()], amount: Annotated[str, Form()],
                           description: Annotated[str, Form()] = ""):
    source = own_source(db, user, source_id)
    _add_transaction(request, db, user, source.type, date_, entry, amount, description,
                     source_id=source.id)
    return redirect(f"/sources/{source.id}?year={tax_year_of(date_)}")


@app.post("/sources/{source_id}/trips")
def add_trip(request: Request, db: DB, user: CurrentUser, source_id: int,
             date_: Annotated[date, Form(alias="date")], miles: Annotated[str, Form()],
             vehicle: Annotated[str, Form()] = "car_van",
             description: Annotated[str, Form()] = ""):
    source = own_source(db, user, source_id)
    try:
        tenths = parse_miles_tenths(miles)
        if vehicle not in ("car_van", "motorcycle"):
            raise ValueError("Choose a vehicle type")
    except ValueError as e:
        flash(request, str(e), "error")
    else:
        db.add(MileageTrip(source_id=source.id, date=date_, miles_tenths=tenths,
                           vehicle=vehicle, description=description.strip()[:255]))
        db.commit()
        flash(request, f"Logged {tenths / 10:g} business miles.")
    return redirect(f"/sources/{source.id}?year={tax_year_of(date_)}#mileage")


@app.post("/trips/{trip_id}/delete")
def delete_trip(request: Request, db: DB, user: CurrentUser, trip_id: int):
    trip = db.get(MileageTrip, trip_id)
    if not trip:
        raise HTTPException(404)
    source = own_source(db, user, trip.source_id)
    year = tax_year_of(trip.date)
    db.delete(trip)
    db.commit()
    flash(request, "Trip deleted.")
    return redirect(f"/sources/{source.id}?year={year}#mileage")


# ------------------------------------------------------------- property

@app.get("/properties/{property_id}")
def property_page(request: Request, db: DB, user: CurrentUser, property_id: int, year: str | None = None):
    prop = own_property(db, user, property_id)
    year = pick_year(year)
    start, end = tax_year_bounds(year)
    txns = db.scalars(
        select(Transaction).where(Transaction.property_id == prop.id,
                                  Transaction.date >= start, Transaction.date <= end)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    ).all()
    income = sum(t.amount_pence for t in txns if t.kind == "income")
    expenses = sum(t.amount_pence for t in txns if t.kind == "expense")
    return render(request, "property.html", user=user, prop=prop, year=year,
                  years=year_options(year), txns=txns, income=income, expenses=expenses,
                  today=date.today(), source_type="uk-property",
                  post_url=f"/properties/{prop.id}")


@app.post("/properties/{property_id}/transactions")
def add_property_transaction(request: Request, db: DB, user: CurrentUser, property_id: int,
                             date_: Annotated[date, Form(alias="date")],
                             entry: Annotated[str, Form()], amount: Annotated[str, Form()],
                             description: Annotated[str, Form()] = ""):
    prop = own_property(db, user, property_id)
    _add_transaction(request, db, user, "uk-property", date_, entry, amount, description,
                     property_id=prop.id)
    return redirect(f"/properties/{prop.id}?year={tax_year_of(date_)}")


# --------------------------------------------------------- shared actions

def _transaction_owner_url(db: Session, user: User, txn: Transaction) -> str:
    if txn.source_id:
        return f"/sources/{own_source(db, user, txn.source_id).id}"
    return f"/properties/{own_property(db, user, txn.property_id).id}"


@app.post("/transactions/{txn_id}/delete")
def delete_transaction(request: Request, db: DB, user: CurrentUser, txn_id: int):
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404)
    url = _transaction_owner_url(db, user, txn)
    year = tax_year_of(txn.date)
    db.delete(txn)
    db.commit()
    flash(request, "Transaction deleted.")
    return redirect(f"{url}?year={year}")


async def _import(request, db, user, file: UploadFile, source_type, **owner):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        flash(request, "That file is over 2 MB.", "error")
        return
    rows, errors = parse_transactions(data, source_type)
    if errors:
        for e in errors[:10]:
            flash(request, e, "error")
        if len(errors) > 10:
            flash(request, f"…and {len(errors) - 10} more problems.", "error")
        flash(request, "Nothing was imported. Fix the rows above and try again.", "error")
        return
    if not rows:
        flash(request, "The file has no rows to import.", "error")
        return
    db.add_all(Transaction(created_by_id=user.id, **owner, **r) for r in rows)
    db.commit()
    flash(request, f"Imported {len(rows)} transactions.")


@app.post("/sources/{source_id}/import")
async def import_source(request: Request, db: DB, user: CurrentUser, source_id: int, file: UploadFile):
    source = own_source(db, user, source_id)
    await _import(request, db, user, file, source.type, source_id=source.id)
    return redirect(f"/sources/{source.id}")


@app.post("/properties/{property_id}/import")
async def import_property(request: Request, db: DB, user: CurrentUser, property_id: int, file: UploadFile):
    prop = own_property(db, user, property_id)
    await _import(request, db, user, file, "uk-property", property_id=prop.id)
    return redirect(f"/properties/{prop.id}")
