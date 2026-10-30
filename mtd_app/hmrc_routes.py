import json
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from . import categories, fraud_headers, hmrc_service
from .hmrc_client import HmrcClient, HmrcError
from .hmrc_config import load_config, new_state
from .models import IncomeSource, Submission
from .periods import tax_year_bounds, tax_year_of
from .web import DB, CurrentUser, flash, redirect, render

router = APIRouter(prefix="/hmrc")

# HMRC's own NINO format rule (the one its APIs validate against)
NINO_RE = re.compile(
    r"^((?!(BG|GB|KN|NK|NT|TN|ZZ)|(D|F|I|Q|U|V)[A-Z]|[A-Z](D|F|I|O|Q|U|V))[A-Z]{2})[0-9]{6}[A-D]$")
YEAR_RE = re.compile(r"^\d{4}/\d{2}$")


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
    if client:
        ctx["env"] = client.config.env
        headers = fraud_headers.build(request, user.id, client.config)
        ctx["fraud_sent"], ctx["fraud_missing"] = headers, fraud_headers.missing(headers)
        if user.hmrc and user.nino:
            try:
                token = hmrc_service.access_token(db, user, client)
                start, end = tax_year_bounds(year)
                ctx["obligations"] = _obligation_rows(
                    user, client.obligations(token, headers, user.nino, start, end))
            except hmrc_service.NotConnected as e:
                flash(request, str(e), "error")
            except HmrcError as e:
                ctx["obligations_error"] = e.describe()

    ctx["submissions"] = (db.query(Submission).filter(Submission.user_id == user.id)
                          .order_by(Submission.id.desc()).limit(20).all())
    return render(request, "hmrc.html", **ctx)


def _obligation_rows(user, obligations: list[dict]) -> list[dict]:
    by_id = {s.hmrc_business_id: s for s in user.sources if s.hmrc_business_id}
    rows = []
    for ob in obligations:
        source = by_id.get(ob.get("businessId"))
        for d in ob.get("obligationDetails", []):
            rows.append({
                "source": source,
                "business_id": ob.get("businessId"),
                "type": ob.get("typeOfBusiness"),
                "start": date.fromisoformat(d["periodStartDate"]),
                "end": date.fromisoformat(d["periodEndDate"]),
                "due": date.fromisoformat(d["dueDate"]),
                "status": d.get("status"),
                "received": d.get("receivedDate"),
            })
    return sorted(rows, key=lambda r: (r["end"], r["type"] or ""))


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

@router.post("/calculation")
def trigger_calculation(request: Request, db: DB, user: CurrentUser, client: Client,
                        year: Annotated[str, Form()]):
    client = _require(client)
    year = _year(year)
    try:
        token = hmrc_service.access_token(db, user, client)
        fraud = fraud_headers.build(request, user.id, client.config)
        calc_id = client.trigger_calculation(token, fraud, user.nino or "", year)
    except (hmrc_service.NotConnected, HmrcError) as e:
        flash(request, e.describe() if isinstance(e, HmrcError) else str(e), "error")
        return redirect(f"/hmrc?year={year}")
    return redirect(f"/hmrc/calculation?year={year}&id={calc_id}")


@router.get("/calculation")
def show_calculation(request: Request, db: DB, user: CurrentUser, client: Client, year: str, id: str):
    client = _require(client)
    year = _year(year)
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", id):
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
    tax = (calc or {}).get("calculation", {}).get("taxCalculation", {})
    return render(request, "hmrc_calculation.html", user=user, year=year, calc_id=id,
                  pending=pending, error=error, tax=tax,
                  raw=json.dumps(calc, indent=2) if calc else None)


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
