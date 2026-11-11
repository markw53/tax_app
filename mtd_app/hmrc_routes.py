import json
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import annual, categories, fraud_headers, hmrc_service
from .hmrc_client import HmrcClient, HmrcError
from .hmrc_config import load_config, new_state
from .models import FinalDeclaration, IncomeSource, Submission
from .periods import quarters, tax_year_bounds, tax_year_of
from .records import totals_for
from .web import DB, CurrentUser, flash, redirect, render

router = APIRouter(prefix="/hmrc")

# HMRC's own NINO format rule (the one its APIs validate against)
NINO_RE = re.compile(
    r"^((?!(BG|GB|KN|NK|NT|TN|ZZ)|(D|F|I|Q|U|V)[A-Z]|[A-Z](D|F|I|O|Q|U|V))[A-Z]{2})[0-9]{6}[A-D]$")
YEAR_RE = re.compile(r"^\d{4}/\d{2}$")
CALC_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def get_hmrc_client() -> HmrcClient | None:
    config = load_config()
    return HmrcClient(config) if config else None


Client = Annotated[HmrcClient | None, Depends(get_hmrc_client)]


def _year(year: str | None) -> str:
    return year if year and YEAR_RE.fullmatch(year) else tax_year_of(date.today())


def _require(client: HmrcClient | None) -> HmrcClient:
    if client is None:
        raise HTTPException(503, "HMRC isn't set up: add HMRC_CLIENT_ID and HMRC_CLIENT_SECRET to .env.hmrc")
    return client


def _own_source(user, source_id: int) -> IncomeSource:
    source = next((s for s in user.sources if s.id == source_id), None)
    if not source:
        raise HTTPException(404)
    return source


@router.get("")
def hmrc_home(request: Request, db: DB, user: CurrentUser, client: Client, year: str | None = None):
    year = _year(year)
    ctx = {"user": user, "year": year, "configured": client is not None, "obligations": [],
           "businesses": request.session.get("hmrc_businesses", [])}
    hmrc_obligations: list[dict] = []
    if client:
        ctx["env"] = client.config.env
        headers = fraud_headers.build(request, user.id, client.config)
        ctx["fraud_sent"], ctx["fraud_missing"] = headers, fraud_headers.missing(headers)
        if user.hmrc and user.nino:
            try:
                token = hmrc_service.access_token(db, user, client)
                start, end = tax_year_bounds(year)
                hmrc_obligations = client.obligations(token, headers, user.nino, start, end)
            except hmrc_service.NotConnected as e:
                flash(request, str(e), "error")
            except HmrcError as e:
                ctx["obligations_error"] = e.describe()

    submissions = (db.query(Submission).filter(Submission.user_id == user.id)
                   .order_by(Submission.id.desc()).all())
    ctx["obligations"] = _update_rows(user, year, hmrc_obligations, submissions)
    ctx["year_end"] = _year_end(db, user, year, submissions, ctx.get("env"))
    ctx["submissions"] = submissions[:20]
    return render(request, "hmrc.html", **ctx)


def _update_rows(user, year: str, obligations: list[dict], submissions: list[Submission]) -> list[dict]:
    """One row per linked source per quarter of the year, with HMRC's
    status where its obligations list has a matching period. Our own
    quarters are the backbone so updates can still be sent if HMRC's list
    is late or incomplete (as it always is in the sandbox)."""
    hmrc_status = {}
    for ob in obligations:
        for d in ob.get("obligationDetails", []):
            hmrc_status[(ob.get("businessId"), d.get("periodEndDate"))] = d
    last_sent = {}
    for s in submissions:  # newest first
        if s.succeeded and s.kind == "update":
            last_sent.setdefault((s.source_id, s.period_end), s)

    rows = []
    today = date.today()
    for source in sorted(user.sources, key=lambda s: (s.type, s.name)):
        if not source.hmrc_business_id:
            continue
        for q in quarters(year, calendar=user.calendar_quarters):
            hmrc = hmrc_status.get((source.hmrc_business_id, q.end.isoformat()))
            rows.append({
                "source": source, "quarter": q,
                "due": date.fromisoformat(hmrc["dueDate"]) if hmrc else q.deadline,
                "hmrc_status": hmrc.get("status") if hmrc else None,
                "sent": last_sent.get((source.id, q.end)),
                "ended": q.end < today,
            })
    return rows


def _year_end(db, user, year: str, submissions: list[Submission], env: str | None) -> dict:
    """Where each linked source stands on its end-of-year steps."""
    sent = {}
    for s in submissions:  # newest first
        if s.succeeded and s.kind == "annual" and s.tax_year == year and s.env == env:
            sent.setdefault(s.source_id, s)
    rows = [{"source": source, "sent": sent.get(source.id),
             "entered": bool(hmrc_service.annual_figures(db, source, year))}
            for source in sorted(user.sources, key=lambda s: (s.type, s.name))
            if source.hmrc_business_id]
    declared = (db.query(FinalDeclaration)
                .filter_by(user_id=user.id, tax_year=year, env=env, succeeded=True)
                .order_by(FinalDeclaration.id.desc()).first())
    year_end = tax_year_bounds(year)[1]
    return {"rows": rows, "declared": declared, "ended": date.today() > year_end,
            "ends": year_end, "deadline": date(year_end.year + 1, 1, 31),
            "supported": int(year[:4]) >= annual.FIRST_TAX_YEAR}


@router.post("/nino")
def set_nino(request: Request, db: DB, user: CurrentUser, nino: Annotated[str, Form()]):
    cleaned = re.sub(r"\s", "", nino).upper()
    if not NINO_RE.fullmatch(cleaned):
        flash(request, "That doesn't look like a National Insurance number (e.g. AB123456C).", "error")
    else:
        user.nino = cleaned
        db.commit()
        flash(request, "National Insurance number saved.")
    return redirect("/hmrc")


# ----------------------------------------------------------------- OAuth

@router.get("/connect")
def connect(request: Request, user: CurrentUser, client: Client):
    client = _require(client)
    state = new_state()
    request.session["hmrc_state"] = state
    return RedirectResponse(client.authorize_url(state), status_code=303)


@router.get("/callback")
def callback(request: Request, db: DB, user: CurrentUser, client: Client,
             code: str | None = None, state: str | None = None, error: str | None = None,
             error_description: str | None = None):
    client = _require(client)
    expected = request.session.pop("hmrc_state", None)
    if error:
        flash(request, f"HMRC didn't authorise the connection: {error_description or error}", "error")
    elif not code or not state or state != expected:
        flash(request, "The HMRC sign-in couldn't be verified. Please try again.", "error")
    else:
        try:
            hmrc_service.store_tokens(db, user, client.config.env, client.exchange_code(code))
            flash(request, f"Connected to HMRC ({client.config.env}).")
        except HmrcError as e:
            flash(request, f"HMRC sign-in failed: {e.describe()}", "error")
    return redirect("/hmrc")


@router.post("/disconnect")
def disconnect(request: Request, db: DB, user: CurrentUser):
    hmrc_service.disconnect(db, user)
    flash(request, "Disconnected from HMRC. Your records are unchanged.")
    return redirect("/hmrc")


# ------------------------------------------------------- business links

@router.post("/businesses")
def fetch_businesses(request: Request, db: DB, user: CurrentUser, client: Client):
    client = _require(client)
    if not user.nino:
        flash(request, "Add your National Insurance number first.", "error")
        return redirect("/hmrc")
    try:
        token = hmrc_service.access_token(db, user, client)
        fraud = fraud_headers.build(request, user.id, client.config)
        found = client.list_businesses(token, fraud, user.nino)
    except (hmrc_service.NotConnected, HmrcError) as e:
        flash(request, e.describe() if isinstance(e, HmrcError) else str(e), "error")
        return redirect("/hmrc")
    request.session["hmrc_businesses"] = [
        {"id": b.get("businessId"), "type": b.get("typeOfBusiness"), "name": b.get("tradingName") or ""}
        for b in found if b.get("businessId")
    ]
    flash(request, f"HMRC has {len(found)} business(es) for you. Link each one below.")
    return redirect("/hmrc#links")


@router.post("/link")
def link(request: Request, db: DB, user: CurrentUser,
         source_id: Annotated[int, Form()], business_id: Annotated[str, Form()]):
    source = _own_source(user, source_id)
    known = {b["id"]: b for b in request.session.get("hmrc_businesses", [])}
    if business_id == "":
        source.hmrc_business_id = None
        flash(request, f"Unlinked '{source.name}'.")
    elif business_id not in known or known[business_id]["type"] != source.type:
        flash(request, "Choose one of your HMRC businesses of the same type.", "error")
        return redirect("/hmrc#links")
    else:
        source.hmrc_business_id = business_id
        flash(request, f"Linked '{source.name}' to HMRC business {business_id}.")
    db.commit()
    return redirect("/hmrc#links")


# -------------------------------------------------------------- updates

def _parse_period(year: str, end: str) -> tuple[str, date]:
    if not YEAR_RE.fullmatch(year):
        raise HTTPException(400, "Bad tax year")
    try:
        return year, date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Bad period end date")


@router.get("/update/{source_id}")
def review_update(request: Request, db: DB, user: CurrentUser, source_id: int, year: str, end: str):
    source = _own_source(user, source_id)
    year, period_end = _parse_period(year, end)
    try:
        body = hmrc_service.build_update(db, source, year, period_end)
    except ValueError as e:
        flash(request, str(e), "error")
        return redirect("/hmrc")
    return render(request, "hmrc_update.html", user=user, source=source, year=year,
                  drops_expenses=hmrc_service.claims_income_allowance(db, source, year),
                  period_start=tax_year_bounds(year)[0], period_end=period_end,
                  lines=_payload_lines(source.type, body), body=json.dumps(body, indent=2))


def _payload_lines(source_type: str, body: dict) -> list[tuple[str, str, float]]:
    if source_type == "self-employment":
        sections = [("income", body.get("periodIncome", {})), ("expense", body.get("periodExpenses", {}))]
    else:
        prop = body.get("ukProperty", {})
        sections = [("income", prop.get("income", {})), ("expense", prop.get("expenses", {}))]
    return [(kind, categories.label(source_type, kind, k), v)
            for kind, values in sections for k, v in values.items()]


@router.post("/update/{source_id}")
def send_update(request: Request, db: DB, user: CurrentUser, client: Client, source_id: int,
                year: Annotated[str, Form()], end: Annotated[str, Form()]):
    client = _require(client)
    source = _own_source(user, source_id)
    year, period_end = _parse_period(year, end)
    fraud = fraud_headers.build(request, user.id, client.config)
    try:
        record = hmrc_service.submit_update(db, user, source, year, period_end, client, fraud)
    except (ValueError, hmrc_service.NotConnected) as e:
        flash(request, str(e), "error")
        return redirect("/hmrc")
    if record.succeeded:
        flash(request, f"HMRC accepted the update for '{source.name}' to {period_end:%-d %b %Y}.")
    else:
        flash(request, f"HMRC rejected the update: {record.error}", "error")
    return redirect(f"/hmrc?year={year}")


# ---------------------------------------------------------- calculation

def _start_calculation(request, db, user, client, year: str, calculation_type: str):
    try:
        token = hmrc_service.access_token(db, user, client)
        fraud = fraud_headers.build(request, user.id, client.config)
        calc_id = client.trigger_calculation(token, fraud, user.nino or "", year, calculation_type)
    except (hmrc_service.NotConnected, HmrcError) as e:
        flash(request, e.describe() if isinstance(e, HmrcError) else str(e), "error")
        return redirect(f"/hmrc?year={year}")
    return redirect(f"/hmrc/calculation?year={year}&id={calc_id}")


@router.post("/calculation")
def trigger_calculation(request: Request, db: DB, user: CurrentUser, client: Client,
                        year: Annotated[str, Form()]):
    return _start_calculation(request, db, user, _require(client), _year(year), "in-year")


@router.post("/final-calculation")
def trigger_final_calculation(request: Request, db: DB, user: CurrentUser, client: Client,
                              year: Annotated[str, Form()]):
    """Ask HMRC for the final calculation, the first step of the tax return."""
    client = _require(client)
    year = _year(year)
    if date.today() <= tax_year_bounds(year)[1]:
        flash(request, f"The final calculation for {year} can only be requested after the tax year ends.", "error")
        return redirect(f"/hmrc?year={year}")
    return _start_calculation(request, db, user, client, year, "intent-to-finalise")


@router.get("/calculation")
def show_calculation(request: Request, db: DB, user: CurrentUser, client: Client, year: str, id: str):
    client = _require(client)
    year = _year(year)
    if not CALC_ID_RE.fullmatch(id):
        raise HTTPException(400, "Bad calculation ID")
    calc, pending, error = None, False, None
    try:
        token = hmrc_service.access_token(db, user, client)
        fraud = fraud_headers.build(request, user.id, client.config)
        calc = client.get_calculation(token, fraud, user.nino or "", year, id)
    except hmrc_service.NotConnected as e:
        error = str(e)
    except HmrcError as e:
        # HMRC returns 404 until the calculation has finished
        pending = e.status == 404
        error = None if pending else e.describe()
    calc = calc or {}
    tax = calc.get("calculation", {}).get("taxCalculation", {})
    messages = calc.get("messages") or {}
    declared = (db.query(FinalDeclaration)
                .filter_by(user_id=user.id, tax_year=year, env=client.config.env, succeeded=True)
                .order_by(FinalDeclaration.id.desc()).first())
    return render(request, "hmrc_calculation.html", user=user, year=year, calc_id=id,
                  pending=pending, error=error, tax=tax, messages=messages,
                  final=calc.get("metadata", {}).get("calculationType") == "intent-to-finalise",
                  declared=declared, raw=json.dumps(calc, indent=2) if calc else None)


@router.post("/declaration")
def final_declaration(request: Request, db: DB, user: CurrentUser, client: Client,
                      year: Annotated[str, Form()], id: Annotated[str, Form()],
                      agree: Annotated[str, Form()] = ""):
    """Agree HMRC's final calculation. This submits the tax return."""
    client = _require(client)
    year = _year(year)
    if not CALC_ID_RE.fullmatch(id):
        raise HTTPException(400, "Bad calculation ID")
    back = f"/hmrc/calculation?year={year}&id={id}"
    if agree != "yes":
        flash(request, "Tick the box to agree to the declaration before submitting.", "error")
        return redirect(back)
    fraud = fraud_headers.build(request, user.id, client.config)
    try:
        record = hmrc_service.submit_final_declaration(db, user, year, id, client, fraud)
    except (ValueError, hmrc_service.NotConnected) as e:
        flash(request, str(e), "error")
        return redirect(back)
    if not record.succeeded:
        flash(request, f"HMRC didn't accept the declaration: {record.error}", "error")
        return redirect(back)
    flash(request, f"HMRC has received your final declaration for {year}. Your tax return for the year is "
                   "submitted. It can take up to an hour to show as complete in HMRC's records.")
    return redirect(f"/hmrc?year={year}")


# ------------------------------------------ allowances and adjustments

def _annual_source(user, source_id: int, year: str) -> IncomeSource:
    if not YEAR_RE.fullmatch(year):
        raise HTTPException(400, "Bad tax year")
    return _own_source(user, source_id)


def _annual_form(request, db, user, source, year: str, values: dict):
    has_expenses = bool(totals_for(db, source, *tax_year_bounds(year)).total_expenses)
    return render(request, "hmrc_annual.html", user=user, source=source, year=year,
                  fields=annual.FIELDS[source.type], values=values, has_expenses=has_expenses,
                  exemptions=annual.CLASS4_EXEMPTIONS)


@router.get("/annual/{source_id}")
def annual_form(request: Request, db: DB, user: CurrentUser, source_id: int, year: str):
    source = _annual_source(user, source_id, year)
    try:
        annual.check_tax_year(year)
    except ValueError as e:
        flash(request, str(e), "error")
        return redirect(f"/hmrc?year={year}")
    data = hmrc_service.annual_figures(db, source, year)
    values = {k: f"{v / 100:.2f}" if isinstance(v, int) and not isinstance(v, bool) else v
              for k, v in data.items()}
    return _annual_form(request, db, user, source, year, values)


@router.post("/annual/{source_id}")
async def save_annual(request: Request, db: DB, user: CurrentUser, source_id: int):
    form = await request.form()
    year = str(form.get("year", ""))
    source = _annual_source(user, source_id, year)
    try:
        annual.check_tax_year(year)
        data = annual.parse_form(source.type, form)
    except ValueError as e:
        flash(request, str(e), "error")
        return _annual_form(request, db, user, source, year, dict(form))
    hmrc_service.save_annual_figures(db, source, year, data)
    return redirect(f"/hmrc/annual/{source.id}/review?year={year}")


@router.get("/annual/{source_id}/review")
def review_annual(request: Request, db: DB, user: CurrentUser, source_id: int, year: str):
    source = _annual_source(user, source_id, year)
    try:
        body = hmrc_service.build_annual(db, source, year)
    except ValueError as e:
        flash(request, str(e), "error")
        return redirect(f"/hmrc/annual/{source.id}?year={year}")
    data = hmrc_service.annual_figures(db, source, year)
    return render(request, "hmrc_annual_review.html", user=user, source=source, year=year,
                  lines=annual.lines(source.type, data), body=json.dumps(body, indent=2),
                  drops_expenses=annual.claims_income_allowance(source.type, data))


@router.post("/annual/{source_id}/send")
def send_annual(request: Request, db: DB, user: CurrentUser, client: Client, source_id: int,
                year: Annotated[str, Form()]):
    client = _require(client)
    source = _annual_source(user, source_id, year)
    fraud = fraud_headers.build(request, user.id, client.config)
    try:
        record = hmrc_service.submit_annual(db, user, source, year, client, fraud)
    except (ValueError, hmrc_service.NotConnected) as e:
        flash(request, str(e), "error")
        return redirect(f"/hmrc?year={year}")
    if not record.succeeded:
        flash(request, f"HMRC rejected the allowances and adjustments: {record.error}", "error")
    else:
        flash(request, f"HMRC accepted the {year} allowances and adjustments for '{source.name}'.")
        if hmrc_service.claims_income_allowance(db, source, year):
            flash(request, "Because you're claiming the £1,000 allowance, resend your latest quarterly "
                           f"update for '{source.name}' so HMRC has it without expenses.")
    return redirect(f"/hmrc?year={year}#year-end")


# ------------------------------------------------------- fraud headers

@router.post("/fraud-check")
def fraud_check(request: Request, db: DB, user: CurrentUser, client: Client):
    client = _require(client)
    if client.config.env != "sandbox":
        flash(request, "HMRC's header checker only runs in the sandbox.", "error")
        return redirect("/hmrc")
    try:
        token = hmrc_service.access_token(db, user, client)
        result = client.validate_fraud_headers(token, fraud_headers.build(request, user.id, client.config))
    except (hmrc_service.NotConnected, HmrcError) as e:
        flash(request, e.describe() if isinstance(e, HmrcError) else str(e), "error")
        return redirect("/hmrc")
    kind = "ok" if result.get("code") == "VALID_HEADERS" else "error"
    flash(request, f"HMRC header check: {result.get('message', result.get('code'))}", kind)
    for item in (result.get("errors") or []) + (result.get("warnings") or []):
        flash(request, f"{', '.join(item.get('headers', []))}: {item.get('message')}", "error")
    return redirect("/hmrc#headers")
