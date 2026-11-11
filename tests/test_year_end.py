"""End of year: allowances and adjustments, final calculation, final declaration."""
import json

import httpx
import pytest

from fastapi.testclient import TestClient

from mtd_app import annual
from mtd_app.auth import hash_password
from mtd_app.main import app
from mtd_app.models import FinalDeclaration, Submission, User

from .conftest import sign_in, totp_secret
from .test_hmrc import NINO, PASSWORD, FakeHmrc, connect, env, logged_in  # noqa: F401

SE_BUSINESS = "XAIS00000000001"


class YearEndHmrc(FakeHmrc):
    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        authorised = req.headers.get("Authorization", "").startswith("Bearer access-")
        if authorised and "/annual/" in path and req.method == "PUT":
            self.requests.append(req)
            if path.endswith("/annual/2027-28"):
                return httpx.Response(400, headers={"X-CorrelationId": "corr-bad"}, json={
                    "code": "RULE_TAX_YEAR_NOT_SUPPORTED", "message": "Tax year not supported"})
            return httpx.Response(204, headers={"X-CorrelationId": "corr-annual"})
        if authorised and path.endswith("/trigger/intent-to-finalise"):
            self.requests.append(req)
            return httpx.Response(202, json={"calculationId": "final-1"})
        if authorised and req.method == "GET" and path.endswith(("/final-1", "/final-errors")):
            self.requests.append(req)
            body = {"metadata": {"calculationType": "intent-to-finalise"},
                    "calculation": {"taxCalculation": {
                        "incomeTax": {"totalIncomeTaxDue": 2000, "totalTaxableIncome": 22430},
                        "nics": {"totalNic": 500}, "totalIncomeTaxAndNicsDue": 2500}}}
            if path.endswith("/final-errors"):
                body["messages"] = {"errors": [{"id": "C55317", "text": "Final update missing"}]}
            return httpx.Response(200, json=body)
        if authorised and req.method == "POST" and path.endswith("/final-declaration"):
            self.requests.append(req)
            if "/final-rejected/" in path:
                return httpx.Response(400, headers={"X-CorrelationId": "corr-no"}, json={
                    "code": "RULE_RECENT_SUBMISSIONS_EXIST", "message": "More recent submissions exist"})
            return httpx.Response(204, headers={"X-CorrelationId": "corr-declared"})
        return super().__call__(req)


@pytest.fixture
def hmrc():
    return YearEndHmrc()


@pytest.fixture
def client(env):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    c.post("/hmrc/businesses")
    c.post("/hmrc/link", data={"source_id": env["source"], "business_id": SE_BUSINESS})
    return c


# ------------------------------------------------------------ payloads

def test_self_employment_payload_sections():
    body = annual.payload("self-employment", {
        "annualInvestmentAllowance": 120_050, "goodsAndServicesOwnUse": 4_000,
        "class4NicsExemptionReason": "over-state-pension-age"})
    assert body == {
        "allowances": {"annualInvestmentAllowance": 1200.5},
        "adjustments": {"goodsAndServicesOwnUse": 40.0},
        "nonFinancials": {"class4NicsExemptionReason": "over-state-pension-age"},
    }


def test_self_employment_with_nothing_entered_cant_be_sent():
    with pytest.raises(ValueError, match="nothing to send"):
        annual.payload("self-employment", {})


def test_property_payload_always_says_whether_non_resident():
    assert annual.payload("uk-property", {}) == {
        "ukProperty": {"adjustments": {"nonResidentLandlord": False}}}
    assert annual.payload("uk-property", {"costOfReplacingDomesticItems": 35_000,
                                          "nonResidentLandlord": True}) == {
        "ukProperty": {"allowances": {"costOfReplacingDomesticItems": 350.0},
                       "adjustments": {"nonResidentLandlord": True}}}


@pytest.mark.parametrize("source_type, form, message", [
    ("self-employment", {"tradingIncomeAllowance": "1000.01"}, "more than £1,000"),
    ("self-employment", {"tradingIncomeAllowance": "500", "annualInvestmentAllowance": "1"},
     "other allowances"),
    ("uk-property", {"propertyIncomeAllowance": "500", "privateUseAdjustment": "10"}, "private use"),
    ("self-employment", {"annualInvestmentAllowance": "lots"}, "isn't an amount"),
    ("self-employment", {"annualInvestmentAllowance": "-5"}, "negative"),
    ("self-employment", {"class4NicsExemptionReason": "bored"}, "from the list"),
])
def test_form_validation(source_type, form, message):
    with pytest.raises(ValueError, match=message):
        annual.parse_form(source_type, form)


def test_blank_and_zero_fields_are_left_out():
    assert annual.parse_form("self-employment", {"annualInvestmentAllowance": " ",
                                                 "allowanceOnSales": "0",
                                                 "balancingChargeOther": "£1,250"}) == {
        "balancingChargeOther": 125_000}


# -------------------------------------------- allowances and adjustments

def test_enter_review_and_send_annual_submission(client, env, session_factory, hmrc):
    src = env["source"]
    assert "None entered" in client.get("/hmrc?year=2026/27").text

    review = client.post(f"/hmrc/annual/{src}", data={
        "year": "2026/27", "annualInvestmentAllowance": "1,200.50", "class4NicsExemptionReason": ""}).text
    assert "Review allowances" in review and "£1,200.50" in review
    assert "Entered, not sent" in client.get("/hmrc?year=2026/27").text
    # The form comes back filled in
    assert 'value="1200.50"' in client.get(f"/hmrc/annual/{src}?year=2026/27").text

    page = client.post(f"/hmrc/annual/{src}/send", data={"year": "2026/27"}).text
    assert "HMRC accepted the 2026/27 allowances and adjustments" in page
    assert "2026/27 allowances and adjustments</td>" in page

    put = next(r for r in hmrc.requests if r.method == "PUT")
    assert put.url.path == f"/individuals/business/self-employment/{NINO}/{SE_BUSINESS}/annual/2026-27"
    assert put.headers["Accept"] == "application/vnd.hmrc.5.0+json"
    assert put.headers["Gov-Client-Connection-Method"] == "WEB_APP_VIA_SERVER"
    assert json.loads(put.content) == {"allowances": {"annualInvestmentAllowance": 1200.5}}

    with session_factory() as s:
        sub = s.query(Submission).one()
        assert sub.kind == "annual" and sub.succeeded and sub.correlation_id == "corr-annual"
        assert sub.tax_year == "2026/27"


def test_annual_submission_doesnt_mark_the_last_quarter_as_sent(client, env):
    client.post(f"/hmrc/annual/{env['source']}", data={"year": "2025/26", "balancingChargeOther": "10"})
    client.post(f"/hmrc/annual/{env['source']}/send", data={"year": "2025/26"})
    page = client.get("/hmrc?year=2025/26").text
    assert "Resend" not in page and page.count("Review and send") == 4


def test_bad_figures_are_shown_again_with_the_problem(client, env, session_factory):
    page = client.post(f"/hmrc/annual/{env['source']}", data={
        "year": "2026/27", "tradingIncomeAllowance": "2000"}).text
    assert "more than £1,000" in page and 'value="2000"' in page
    # Nothing saved, so there's nothing to review
    page = client.get(f"/hmrc/annual/{env['source']}/review?year=2026/27").text
    assert "nothing to send" in page


def test_trading_allowance_replaces_expenses_in_updates(client, env, hmrc):
    src = env["source"]
    before = client.get(f"/hmrc/update/{src}?year=2026/27&end=2026-07-05").text
    assert "£19.99" in before
    assert "recorded expenses for 2026/27" in client.get(f"/hmrc/annual/{src}?year=2026/27").text

    review = client.post(f"/hmrc/annual/{src}", data={"year": "2026/27", "tradingIncomeAllowance": "1000"}).text
    assert "your expenses for 2026/27 won't be sent to HMRC" in review
    page = client.post(f"/hmrc/annual/{src}/send", data={"year": "2026/27"}).text
    assert "resend your latest quarterly update" in page

    after = client.get(f"/hmrc/update/{src}?year=2026/27&end=2026-07-05").text
    assert "£19.99" not in after and "Expenses are left out" in after
    client.post(f"/hmrc/update/{src}", data={"year": "2026/27", "end": "2026-07-05"})
    update = next(r for r in hmrc.requests if "/cumulative/" in r.url.path)
    body = json.loads(update.content)
    assert "periodExpenses" not in body and body["periodIncome"]["turnover"] == 2500.0


def test_allowance_cant_exceed_income(client, env):
    # No income recorded in 2025/26
    page = client.post(f"/hmrc/annual/{env['source']}", data={
        "year": "2025/26", "tradingIncomeAllowance": "1000"}).text
    assert "more than your income for the year" in page


def test_years_before_2025_26_not_supported(client, env):
    page = client.get(f"/hmrc/annual/{env['source']}?year=2024/25").text
    assert "2025/26 onwards" in page
    page = client.post(f"/hmrc/annual/{env['source']}", data={
        "year": "2024/25", "balancingChargeOther": "10"}).text
    assert "2025/26 onwards" in page


def test_rejected_annual_submission_is_recorded(client, env, session_factory):
    client.post(f"/hmrc/annual/{env['source']}", data={"year": "2027/28", "balancingChargeOther": "10"})
    page = client.post(f"/hmrc/annual/{env['source']}/send", data={"year": "2027/28"}).text
    assert "HMRC rejected the allowances and adjustments" in page and "RULE_TAX_YEAR_NOT_SUPPORTED" in page
    with session_factory() as s:
        sub = s.query(Submission).one()
        assert not sub.succeeded and sub.kind == "annual" and sub.status_code == 400


def test_other_users_annual_figures_not_accessible(client, env, session_factory):
    with session_factory() as s:
        s.add(User(email="b@example.com", name="Sam", password_hash=hash_password(PASSWORD),
                   totp_secret=totp_secret()))
        s.commit()
    other = TestClient(app)
    sign_in(other, "b@example.com", PASSWORD)
    src = env["source"]
    assert other.get(f"/hmrc/annual/{src}?year=2026/27").status_code == 404
    assert other.post(f"/hmrc/annual/{src}", data={"year": "2026/27"}).status_code == 404
    assert other.get(f"/hmrc/annual/{src}/review?year=2026/27").status_code == 404
    assert other.post(f"/hmrc/annual/{src}/send", data={"year": "2026/27"}).status_code == 404


# ------------------------------------- final calculation and declaration

def test_final_calculation_waits_for_the_year_to_end(client, hmrc):
    assert "Available once the tax year has ended" in client.get("/hmrc?year=2026/27").text
    page = client.post("/hmrc/final-calculation", data={"year": "2026/27"}).text
    assert "only be requested after the tax year ends" in page
    assert not any("intent-to-finalise" in r.url.path for r in hmrc.requests)


def test_final_calculation_then_declaration(client, session_factory, hmrc):
    page = client.post("/hmrc/final-calculation", data={"year": "2025/26"}).text
    assert "HMRC final calculation for 2025/26" in page
    assert "£2,500.00" in page and "£22,430.00" in page
    assert "I declare that the information and tax return I have submitted" in page
    assert any(r.url.path.endswith("/2025-26/trigger/intent-to-finalise") for r in hmrc.requests)

    # Not without agreeing
    page = client.post("/hmrc/declaration", data={"year": "2025/26", "id": "final-1"}).text
    assert "Tick the box" in page
    with session_factory() as s:
        assert s.query(FinalDeclaration).count() == 0

    page = client.post("/hmrc/declaration", data={"year": "2025/26", "id": "final-1", "agree": "yes"}).text
    assert "HMRC has received your final declaration for 2025/26" in page
    assert "Final declaration sent on" in page
    post = next(r for r in hmrc.requests if r.url.path.endswith("/final-declaration"))
    assert post.method == "POST"
    assert post.url.path == f"/individuals/calculations/{NINO}/self-assessment/2025-26/final-1/final-declaration"
    assert post.headers["Accept"] == "application/vnd.hmrc.9.0+json"
    with session_factory() as s:
        d = s.query(FinalDeclaration).one()
        assert d.succeeded and d.calculation_id == "final-1" and d.correlation_id == "corr-declared"

    # The calculation page no longer offers the declaration
    page = client.get("/hmrc/calculation?year=2025/26&id=final-1").text
    assert "You made your final declaration" in page and 'name="agree"' not in page


def test_in_year_calculation_cant_be_declared(client):
    page = client.post("/hmrc/calculation", data={"year": "2026/27"}).text
    assert "Final declaration" not in page and 'name="agree"' not in page


def test_final_calculation_with_errors_cant_be_declared(client):
    page = client.get("/hmrc/calculation?year=2025/26&id=final-errors").text
    assert "Final update missing" in page and 'name="agree"' not in page


def test_rejected_declaration_is_recorded(client, session_factory):
    page = client.post("/hmrc/declaration", data={
        "year": "2025/26", "id": "final-rejected", "agree": "yes"}).text
    assert "didn&#39;t accept the declaration" in page and "RULE_RECENT_SUBMISSIONS_EXIST" in page
    with session_factory() as s:
        d = s.query(FinalDeclaration).one()
        assert not d.succeeded and d.status_code == 400 and d.correlation_id == "corr-no"
    assert "Final declaration sent on" not in client.get("/hmrc?year=2025/26").text
